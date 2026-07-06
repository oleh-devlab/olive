import logging
import inspect
import disnake
from google.genai import types

import core.cache as cache
from core.utils import get_phrases, send_long_message
from modules.llm_context_manager import LLMContextManager
from modules.schedule_agent_tools import ScheduleAgentTools
from modules.schedule_provider import ScheduleProvider

logger = logging.getLogger(__name__)

# Dedicated context manager for schedule channels so it doesn't pollute the main guild context.
# This ensures it uses the same robust clipping (apply_restrictions) as the main bot.
schedule_context_manager = LLMContextManager(context_file_name="schedule_agent_context.json")

UNDO_TIMEOUT_MINUTES = 15
UNDO_TIMEOUT_SECONDS = UNDO_TIMEOUT_MINUTES * 60


async def load_schedule_context():
    await schedule_context_manager.load_from_file()


def _get_schedule_instruction(guild_id: int) -> str:
    agent_prompt = """You are a highly capable scheduling assistant.
Your goal is to help the user manage their dynamic daily schedule, which consists of Tasks, Routines, and TimeBlocks.

The schedule is automatically generated and optimized by an AI CP-SAT solver behind the scenes. You DO NOT need to manually calculate overlapping times or fit tasks yourself. The solver will automatically chunk large tasks, fit them around routines and timeblocks, and assign breaks. Your job is ONLY to manage the raw data (add/edit/remove tasks, routines, and timeblocks).

### 1. Tasks
Tasks are one-off or long-running work items.
- They have a total duration and optional deadline.
- The solver automatically splits them into chunks (default 45 min) with breaks (default 15 min).
- You can override chunk size or break size if the user requests.

### 2. Routines
Routines are recurring habits or events (daily or weekly).
- `fixed`: Occurs at an exact time (e.g., daily standup at 10:00).
- `flexible`: Must be completed before a deadline, but the solver decides *when* to schedule it (e.g., read a book for 30m before 22:00).
- **Never chunk routines**. The solver will schedule them as single, continuous blocks.
- **Never add routines as tasks**. Always use `add_routine` tool for recurring habits.

### 3. TimeBlocks
TimeBlocks are strict periods of "busy time" when the user is unavailable (e.g., doctor appointment, sleep schedule, gym).
- The solver will completely avoid scheduling any tasks or flexible routines during these periods.
- They can be one-time (today only) or daily recurring.
- Use `add_time_block`, `list_time_blocks`, and `remove_time_block` tools to manage them.

### 4. Priorities
- Tasks and routines have a priority from 0 to 10 (default 1).
- Priority 0 is special: it "floats" and the solver will schedule it anywhere it fits best, without trying to push it early.
- Priorities 1 to 10 will try to be scheduled as close to the beginning of the schedule as possible, essentially "sorting" themselves chronologically based on importance.

### 5. Dependencies
- You can use the `depends_on` parameter to create scheduling dependencies.
- **Important**: Tasks and Routines have separate ID spaces. Therefore, a task can ONLY depend on other tasks, and a routine can ONLY depend on other routines. They cannot be interdependent.
- Pass a comma-separated list of IDs (e.g., '1, 3') if a task/routine must be scheduled strictly *after* the items it depends on.

### 6. Skipping Routines
- The `skip_routine` tool is used to mark a routine as completed or as skipped for today or future days.
- The `resume_after` field means the routine is skipped up to and including that date, and will resume the day after.
- You can skip it for a certain number of `days` (default 1 day = skip today), or `resume_after` ().

### General Rules
- Before deleting or editing, always review the list of tasks/routines/time blocks to make sure you've specified the correct ID/index.
- After adding/editing, don't list everything back to the user unless they ask.
- If a tool returns an error, inform the user about the error and ask how they'd like to proceed, or fix your parameters and try again.
"""
    return agent_prompt


class ConfirmUndoView(disnake.ui.View):
    def __init__(self, bot, user_id: int, backup: dict, provider):
        super().__init__(timeout=60)
        self.bot = bot
        self.user_id = user_id
        self.backup = backup
        self.provider = provider

    @disnake.ui.button(label="Yes, undo all", style=disnake.ButtonStyle.danger)
    async def confirm_yes(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        if interaction.author.id != self.user_id:
            return await interaction.response.send_message("This isn't your schedule.", ephemeral=True)

        self.provider.restore_backup(self.user_id, self.backup)
        self.bot.dispatch("schedule_update", interaction.channel.id)

        for child in self.children:
            child.disabled = True
        button.label = "Canceled"
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("All changes have been undone.", ephemeral=True)

    @disnake.ui.button(label="Ні", style=disnake.ButtonStyle.secondary)
    async def confirm_no(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        if interaction.author.id != self.user_id:
            return await interaction.response.send_message("This isn't your schedule.", ephemeral=True)

        for child in self.children:
            child.disabled = True
        button.label = "Left unchanged"
        await interaction.response.edit_message(view=self)


class UndoScheduleView(disnake.ui.View):
    def __init__(self, bot, user_id: int, backup_data: dict, post_run_data: dict):
        super().__init__(timeout=UNDO_TIMEOUT_SECONDS)
        self.bot = bot
        self.user_id = user_id
        self.backup_data = backup_data
        self.post_run_data = post_run_data
        self.provider = ScheduleProvider()

    @disnake.ui.button(
        label=f"Cancel (Unavailable for {UNDO_TIMEOUT_MINUTES} minutes)", style=disnake.ButtonStyle.danger
    )
    async def undo_button(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        if interaction.author.id != self.user_id:
            return await interaction.response.send_message("This isn't your schedule.", ephemeral=True)

        current_state = self.provider.create_backup(self.user_id)

        if current_state != self.post_run_data:
            confirm_view = ConfirmUndoView(self.bot, self.user_id, self.backup_data, self.provider)
            await interaction.response.send_message(
                "Attention! The schedule has changed since this action was taken. Canceling it now will undo both this action and all subsequent ones. Are you sure?",
                view=confirm_view,
                ephemeral=True,
            )
            return

        self.provider.restore_backup(self.user_id, self.backup_data)
        self.bot.dispatch("schedule_update", interaction.channel.id)

        button.disabled = True
        button.label = "Canceled"
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            "The changes have been canceled, and the schedule has been restored to its previous state.", ephemeral=True
        )


async def run_schedule_agent(bot, message: disnake.Message, user_id: int, new_text: str):
    """
    Agentic loop that allows OLIVE to call tools.
    """
    channel_id_str = str(message.channel.id)

    schedule_context_manager.add_user_message(channel_id_str, new_text)

    context = schedule_context_manager.get_context(channel_id_str)
    system_instruction = _get_schedule_instruction(message.guild.id)

    tools_instance = ScheduleAgentTools(user_id)
    provider = ScheduleProvider()
    backup_data = provider.create_backup(user_id)

    # Expose the bound methods as tools
    agent_tools = [
        tools_instance.get_current_schedule,
        tools_instance.list_tasks,
        tools_instance.get_task_info,
        tools_instance.list_time_blocks,
        tools_instance.add_time_block,
        tools_instance.remove_time_block,
        tools_instance.add_task,
        tools_instance.remove_task,
        tools_instance.edit_task,
        tools_instance.spend_task_time,
        tools_instance.add_routine,
        tools_instance.list_routines,
        tools_instance.get_routine_info,
        tools_instance.edit_routine,
        tools_instance.remove_routine,
        tools_instance.skip_routine,
    ]

    reply_config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        max_output_tokens=2500,
        tools=agent_tools,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
    )

    max_iterations = 7
    iteration = 0

    async with message.channel.typing():
        # I knew even as I was writing it that this loop wasn't working;
        # I just hadn't gotten around to rewriting it and passing the responsibility to the SDK yet.
        # "If it's working, don't touch it."
        while iteration < max_iterations:
            iteration += 1

            # Fetch fresh context before each API call
            context = schedule_context_manager.get_context(channel_id_str)

            try:
                response = await cache.llm_client.get_response(
                    context,
                    reply_config,
                    model_priority=get_phrases().get("olive", {}).get("schedule_agent_models_priority", []),
                )
            except Exception as e:
                logger.error("Error in schedule agent get_response: %s", e)
                await message.reply(f"An error occurred while communicating with the model: {e}")
                return

            candidate_tokens = 0
            if hasattr(response, "usage_metadata") and response.usage_metadata is not None:
                prompt_tokens = getattr(response.usage_metadata, "prompt_token_count", 0)
                candidate_tokens = getattr(response.usage_metadata, "candidates_token_count", 0)
                if prompt_tokens > 0:
                    schedule_context_manager.update_latest_user_message_tokens(channel_id_str, prompt_tokens)

            # Check if there are function calls
            function_calls = []
            if response.candidates and response.candidates[0].content:
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        function_calls.append(part.function_call)

            if not function_calls:
                # No function calls, the model responded with text.
                text_response = response.text or ""
                if not text_response:
                    if tools_instance.used_tools:
                        text_response = "The action was completed (the model did not provide a text response)."
                    else:
                        logger.warning("Agent returned empty response")
                        break

                # Append to context securely with token tracking (BEFORE adding used tools footer)
                schedule_context_manager.add_model_message(channel_id_str, text_response, tokens=candidate_tokens)

                if tools_instance.used_tools:
                    # Deduplicate in case SDK auto-retried
                    unique_tools = []
                    for t in tools_instance.used_tools:
                        if t not in unique_tools:
                            unique_tools.append(t)
                    
                    iters_str = f" ({iteration} iteration{'s' if iteration != 1 else ''})"
                    text_response += f"\n\n---\nUsed tools{iters_str}:\n" + "\n".join(f"- {t}" for t in unique_tools)

                kwargs = {"fail_if_not_exists": False, "mention_author": False}
                if tools_instance.schedule_modified:
                    post_run_data = provider.create_backup(user_id)
                    kwargs["view"] = UndoScheduleView(bot, user_id, backup_data, post_run_data)

                await send_long_message(message, text_response, **kwargs)
                break

            # Model made function calls. We must append them as pure dicts to survive json.dump
            model_parts = []
            for fc in function_calls:
                args_dict = dict(fc.args) if fc.args else {}
                model_parts.append({"function_call": {"name": fc.name, "args": args_dict}})

            schedule_context_manager.llm_context[channel_id_str].append(
                {"role": "model", "parts": model_parts, "tokens": candidate_tokens}
            )

            # Execute all function calls
            function_responses = []
            schedule_modified = False

            for fc in function_calls:
                func_name = fc.name
                args = dict(fc.args) if fc.args else {}

                func_to_call = next((f for f in agent_tools if f.__name__ == func_name), None)

                if not func_to_call:
                    result = {"error": f"Unknown function {func_name}"}
                else:
                    try:
                        if inspect.iscoroutinefunction(func_to_call):
                            res = await func_to_call(**args)
                        else:
                            res = func_to_call(**args)

                        result = {"result": res}
                        if func_name in [
                            "add_task", "remove_task", "edit_task", "spend_task_time",
                            "add_routine", "remove_routine", "edit_routine", "skip_routine",
                            "add_time_block", "remove_time_block"
                        ]:
                            schedule_modified = True

                    except Exception as e:
                        logger.warning("Tool execution error for %s: %s", func_name, str(e))
                        result = {"error": str(e)}

                function_responses.append({"function_response": {"name": func_name, "response": result}})

            # Append the function responses to the context as pure dicts
            schedule_context_manager.llm_context[channel_id_str].append(
                {"role": "user", "parts": function_responses, "tokens": 0}  # Will be updated in the next loop iteration
            )

            if schedule_modified:
                bot.dispatch("schedule_update", message.channel.id)

        else:
            # Reached max iterations
            await message.reply("Agent reached the maximum number of tool iterations and was stopped.")

    # Apply clipping and save context
    limit = cache.llm_client.min_context_tokens if cache.llm_client else 128000
    schedule_context_manager.apply_restrictions(max_tokens=limit)
    await schedule_context_manager.write_to_file()
