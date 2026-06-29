import logging
import time
import inspect
from typing import Dict, Any

import disnake
from google.genai import types

import core.cache as cache
from core.utils import get_phrases
from modules.llm_context_manager import LLMContextManager
from modules.schedule_agent_tools import ScheduleAgentTools
from modules.schedule_provider import ScheduleProvider

logger = logging.getLogger(__name__)

# Dedicated context manager for schedule channels so it doesn't pollute the main guild context.
# This ensures it uses the same robust clipping (apply_restrictions) as the main bot.
schedule_context_manager = LLMContextManager(context_file_name="schedule_agent_context.json")


async def load_schedule_context():
    await schedule_context_manager.load_from_file()


def _get_schedule_instruction(guild_id: int) -> str:
    agent_prompt = (
        "You are now operating in the Schedule Management mode. "
        "Your primary goal is to help the user manage their tasks and timetable. "
        "You have access to tools to list tasks, list time blocks, get the generated schedule, add/remove/edit tasks, and spend time. "
        "Use list_tasks() to find task IDs when you need to edit or delete them. "
        "When you use a tool that modifies the schedule, the user's UI will automatically update. "
        "If a tool returns an error, inform the user about the error and ask how they'd like to proceed, or fix your parameters and try again."
    )
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
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id
        self.backup_data = backup_data
        self.post_run_data = post_run_data
        self.provider = ScheduleProvider()

    @disnake.ui.button(label="Скасувати (Undo)", style=disnake.ButtonStyle.danger)
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
        tools_instance.list_time_blocks,
        tools_instance.add_task,
        tools_instance.remove_task,
        tools_instance.edit_task,
        tools_instance.spend_task_time,
    ]

    reply_config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        max_output_tokens=1500,
        tools=agent_tools,
    )

    max_iterations = 7
    iteration = 0

    async with message.channel.typing():
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

                if tools_instance.used_tools:
                    # Deduplicate in case SDK auto-retried
                    unique_tools = []
                    for t in tools_instance.used_tools:
                        if t not in unique_tools:
                            unique_tools.append(t)
                    text_response += "\n\n---\nUsed tools:\n" + "\n".join(f"- {t}" for t in unique_tools)

                # Append to context securely with token tracking
                schedule_context_manager.add_model_message(channel_id_str, text_response, tokens=candidate_tokens)

                kwargs = {"fail_if_not_exists": False, "mention_author": False}
                if tools_instance.schedule_modified:
                    post_run_data = provider.create_backup(user_id)
                    kwargs["view"] = UndoScheduleView(bot, user_id, backup_data, post_run_data)

                await message.reply(text_response, **kwargs)
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
                        if func_name in ["add_task", "remove_task", "edit_task", "spend_task_time"]:
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
