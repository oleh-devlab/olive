import disnake
from disnake.ext import commands
import datetime

import settings
import modules.automatic_timetable as auto_timetable
import modules.timetable_db as timetable_db
import core.utils as utils
from core.time_utils import tz

def hhmm_to_minutes(start_hhmm: str, end_hhmm: str):
    now = datetime.datetime.now(tz)
    sh, sm = map(int, start_hhmm.split(':'))
    eh, em = map(int, end_hhmm.split(':'))
    
    start_dt = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end_dt = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    
    if end_dt <= start_dt:
        end_dt += datetime.timedelta(days=1)
        
    return int(start_dt.timestamp() / 60), int(end_dt.timestamp() / 60)

def minutes_to_hhmm(mins: int) -> str:
    dt = datetime.datetime.fromtimestamp(int(mins) * 60, tz=tz)
    return dt.strftime("%H:%M")

class AutoSchedule(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.slash_command(test_guilds=settings.guilds)
    @commands.is_owner()
    async def get_test_schedule(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        ID = inter.author.id
        try:
            schedule = await auto_timetable.get_schedule(ID)
        except Exception as e:
            await inter.edit_original_response(f"Error: {str(e)}")
            return
        await inter.edit_original_response("Successfully")

        await utils.send_long_message(inter.channel, f"Schedule:\n```{schedule}```")

    @commands.slash_command(test_guilds=settings.guilds)
    @commands.is_owner()
    async def task(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @task.sub_command(name="add", description="Add a new task")
    async def task_add(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str,
        total_dur_min: int,
        description: str = "",
        priority: int = 1,
        session_dur_min: int = 45,
        break_dur_min: int = 15,
        min_session_min: int = None,
        deadline: str = None
    ):
        await inter.response.defer(ephemeral=True)
        try:
            deadline_mins = None
            if deadline:
                try:
                    dt = datetime.datetime.strptime(deadline, "%d.%m.%Y %H:%M")
                    dt = dt.replace(tzinfo=tz)
                    deadline_mins = int(dt.timestamp() / 60)
                except ValueError:
                    await inter.edit_original_response("Invalid deadline format. Use 'DD.MM.YYYY HH:MM'")
                    return

            new_id = timetable_db.add_task(
                user_id=inter.author.id,
                name=name,
                total_dur=total_dur_min,
                description=description,
                priority=priority,
                session_dur=session_dur_min,
                break_dur=break_dur_min,
                min_session=min_session_min,
                deadline=deadline_mins
            )
            await inter.edit_original_response(f"Task '{name}' added successfully with ID {new_id}.")
        except Exception as e:
            await inter.edit_original_response(f"Error: {str(e)}")

    @task.sub_command(name="remove", description="Remove a task by ID")
    async def task_remove(self, inter: disnake.ApplicationCommandInteraction, task_id: int):
        await inter.response.defer(ephemeral=True)
        removed = timetable_db.remove_task(inter.author.id, task_id)
        if removed:
            await inter.edit_original_response(f"Task {task_id} removed successfully.")
        else:
            await inter.edit_original_response(f"Task {task_id} not found.")

    @task.sub_command(name="list", description="List all current tasks")
    async def task_list(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        tasks = timetable_db.list_tasks(inter.author.id)
        if not tasks:
            await inter.edit_original_response("No tasks found.")
            return
        
        lines = ["**Your Tasks:**"]
        for t in tasks:
            lines.append(f"`[ID: {t['id']}]` **{t['name']}** - {t['total_dur']} min (Priority: {t['priority']})")
        
        await utils.send_long_message(inter.channel, "\n".join(lines))
        await inter.edit_original_response("Tasks listed above.")

    @task.sub_command(name="spend", description="Mark time spent on a task")
    async def task_spend(self, inter: disnake.ApplicationCommandInteraction, task_id: int, minutes: int):
        await inter.response.defer(ephemeral=True)
        try:
            is_completed, remaining = timetable_db.spend_task_time(inter.author.id, task_id, minutes)
            if is_completed:
                await inter.edit_original_response(f"Subtracted {minutes} min. Task fully completed and moved to history!")
            else:
                await inter.edit_original_response(f"Subtracted {minutes} min. Remaining duration: {remaining} min.")
        except Exception as e:
            await inter.edit_original_response(f"Error: {str(e)}")

    @task.sub_command(name="edit", description="Edit specific fields of an existing task")
    async def task_edit(
        self,
        inter: disnake.ApplicationCommandInteraction,
        task_id: int,
        name: str = None,
        total_dur_min: int = None,
        description: str = None,
        priority: int = None,
        session_dur_min: int = None,
        break_dur_min: int = None,
        min_session_min: int = None,
        deadline: str = None
    ):
        await inter.response.defer(ephemeral=True)
        try:
            updates = {}
            if name is not None: updates["name"] = name.replace('\t', ' ').replace('\n', ' ').strip()
            if total_dur_min is not None: updates["total_dur"] = total_dur_min
            if description is not None: updates["description"] = description.replace('\t', ' ').replace('\n', ' ').strip()
            if priority is not None: updates["priority"] = priority
            if session_dur_min is not None: updates["session_dur"] = session_dur_min
            if break_dur_min is not None: updates["break_dur"] = break_dur_min
            if min_session_min is not None:
                updates["has_min_session"] = 1
                updates["min_session"] = min_session_min
            
            if deadline is not None:
                if deadline.lower() == "none":
                    updates["has_deadline"] = 0
                    updates["deadline"] = 0
                else:
                    dt = datetime.datetime.strptime(deadline, "%d.%m.%Y %H:%M")
                    dt = dt.replace(tzinfo=tz)
                    updates["has_deadline"] = 1
                    updates["deadline"] = int(dt.timestamp() / 60)

            success = timetable_db.edit_task(inter.author.id, task_id, **updates)
            if success:
                await inter.edit_original_response(f"Task {task_id} updated successfully.")
            else:
                await inter.edit_original_response(f"Task {task_id} not found.")
        except ValueError:
            await inter.edit_original_response("Invalid deadline format. Use 'DD.MM.YYYY HH:MM' or 'none'.")
        except Exception as e:
            await inter.edit_original_response(f"Error: {str(e)}")

    @task.sub_command(name="info", description="View detailed information about a task")
    async def task_info(self, inter: disnake.ApplicationCommandInteraction, task_id: int):
        await inter.response.defer(ephemeral=True)
        task = timetable_db.get_task(inter.author.id, task_id)
        if not task:
            await inter.edit_original_response(f"Task {task_id} not found.")
            return

        lines = [
            f"**Task Details (ID: {task['id']})**",
            f"**Name:** {task['name']}",
            f"**Description:** {task['description'] if task['description'].strip() else '(none)'}",
            f"**Priority:** {task['priority']}",
            f"**Total Duration:** {task['total_dur']} min",
            f"**Session:** {task['session_dur']} min  |  **Break:** {task['break_dur']} min"
        ]

        if int(task['has_deadline']):
            dl_str = minutes_to_hhmm(int(task['deadline']))
            lines.insert(3, f"**Deadline:** {dl_str}")
        else:
            lines.insert(3, "**Deadline:** none")
            
        if int(task['has_min_session']):
            lines.append(f"**Min session shortening allowed:** {task['min_session']} min")

        await inter.edit_original_response("\n".join(lines))

    @task.sub_command(name="history", description="List completed tasks")
    async def task_history(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        tasks = timetable_db.list_completed_tasks(inter.author.id)
        if not tasks:
            await inter.edit_original_response("No completed tasks found in history.")
            return
        
        lines = ["**Completed Tasks:**"]
        for t in tasks:
            lines.append(f"`[ID: {t['id']}]` **{t['name']}** (Priority: {t['priority']})")
        
        await utils.send_long_message(inter.channel, "\n".join(lines))
        await inter.edit_original_response("History listed above.")

    @commands.slash_command(test_guilds=settings.guilds)
    @commands.is_owner()
    async def timeblock(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @timeblock.sub_command(name="add", description="Add a new time block")
    async def timeblock_add(
        self,
        inter: disnake.ApplicationCommandInteraction,
        start_time: str,
        end_time: str,
        is_repeatable: bool = True,
        is_every_day: bool = True,
        day_of_week: int = 0
    ):
        await inter.response.defer(ephemeral=True)
        try:
            start_m, end_m = hhmm_to_minutes(start_time, end_time)
            timetable_db.add_time_block(
                user_id=inter.author.id,
                start_mins=start_m,
                end_mins=end_m,
                is_repeatable=1 if is_repeatable else 0,
                is_every_day=1 if is_every_day else 0,
                day_of_week=day_of_week
            )
            await inter.edit_original_response(f"Timeblock added: {start_time} to {end_time}.")
        except Exception as e:
            await inter.edit_original_response(f"Error: {str(e)}")

    @timeblock.sub_command(name="remove", description="Remove a time block by index")
    async def timeblock_remove(self, inter: disnake.ApplicationCommandInteraction, index: int):
        await inter.response.defer(ephemeral=True)
        # Assuming index shown in list is 1-based to be user friendly
        removed = timetable_db.remove_time_block(inter.author.id, index - 1)
        if removed:
            await inter.edit_original_response(f"Timeblock {index} removed successfully.")
        else:
            await inter.edit_original_response(f"Timeblock {index} not found.")

    @timeblock.sub_command(name="list", description="List all time blocks")
    async def timeblock_list(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        blocks = timetable_db.list_time_blocks(inter.author.id)
        if not blocks:
            await inter.edit_original_response("No time blocks found.")
            return
        
        lines = ["**Your Time Blocks:**"]
        for i, b in enumerate(blocks):
            try:
                st = minutes_to_hhmm(int(b['start_time']))
                et = minutes_to_hhmm(int(b['end_time']))
                rep = "Repeatable" if int(b['is_repeatable']) else "One-time"
                lines.append(f"`[{i + 1}]` {st} - {et} ({rep})")
            except Exception as e:
                lines.append(f"`[{i + 1}]` Invalid Block Data")
        
        await utils.send_long_message(inter.channel, "\n".join(lines))
        await inter.edit_original_response("Time blocks listed above.")

def setup(bot):
    bot.add_cog(AutoSchedule(bot))
