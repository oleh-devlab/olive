import disnake
from disnake.ext import commands
import datetime
import traceback
import logging

from modules.inflation_wrapper import add_record, delete_record, get_report
from modules.inflation_calculator.modules.exceptions import (
    InflationCalculatorError,
    ValidationError,
    MissingInflationDataError
)

logger = logging.getLogger(__name__)

class InflationTools(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.slash_command(name="inflation", description="Commands for inflation calculator")
    async def inflation(self, inter: disnake.ApplicationCommandInteraction):
        """Base command for inflation calculator"""
        pass

    @inflation.sub_command(name="add", description="Add a new record to calculate its inflation later")
    async def add(
        self, 
        inter: disnake.ApplicationCommandInteraction, 
        amount: str = commands.Param(description="Amount of money (e.g. 5000)"), 
        date_str: str = commands.Param(description="Date in DD.MM.YYYY format"), 
        comment: str = commands.Param(description="Optional comment for this record", default="")
    ):
        await inter.response.defer(ephemeral=True)
        try:
            date_obj = datetime.datetime.strptime(date_str, "%d.%m.%Y").date()
            record = add_record(inter.author.id, amount, date_obj, comment)
            await inter.edit_original_response(content=f"Record added successfully! ID: {record.get('id')}")
        except ValueError:
            await inter.edit_original_response(content="Invalid date format. Please use DD.MM.YYYY")
        except ValidationError as e:
            await inter.edit_original_response(content=f"Validation error: {e}")
        except InflationCalculatorError as e:
            await inter.edit_original_response(content=f"Inflation calculator error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in inflation add: {traceback.format_exc()}")
            await inter.edit_original_response(content="An unexpected error occurred while adding the record. Please notify the administrator.")

    @inflation.sub_command(name="delete", description="Delete a record by its ID")
    async def delete(
        self, 
        inter: disnake.ApplicationCommandInteraction, 
        record_id: int = commands.Param(description="ID of the record to delete")
    ):
        await inter.response.defer(ephemeral=True)
        try:
            record = delete_record(inter.author.id, record_id)
            await inter.edit_original_response(content=f"Record deleted successfully! (Amount: {record.get('amount')})")
        except ValidationError as e:
            await inter.edit_original_response(content=f"Validation error: {e}")
        except InflationCalculatorError as e:
            await inter.edit_original_response(content=f"Inflation calculator error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in inflation delete: {traceback.format_exc()}")
            await inter.edit_original_response(content="An unexpected error occurred while deleting the record. Please notify the administrator.")

    @inflation.sub_command(name="report", description="Get your personal inflation report")
    async def report(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        try:
            report_data = get_report(inter.author.id)
            
            if not report_data.get('records'):
                await inter.edit_original_response(content="No records available for the report.")
                return

            total_nominal = report_data.get('total_nominal', 0)
            total_adjusted = report_data.get('total_adjusted', 0)
            loss_percent = report_data.get('loss_percent', 0)
            oldest_date = report_data.get('oldest_date')
            
            short_report = (
                f"**Inflation Report**\n"
                f"Total Nominal: `{total_nominal} UAH`\n"
                f"Oldest Record: `{oldest_date}`\n"
                f"Inflation-adjusted equivalent: `{total_adjusted} UAH`\n"
                f"Purchasing power loss: `{loss_percent}%`\n"
            )
            
            full_report = short_report + "\n**Details:**\n"
            for r in report_data.get('records', []):
                rec_id = r.get('id', '?')
                amt = f"{float(r['amount']):.2f}"
                date = r['date']
                comment = f" | {r['comment']}" if r.get('comment') else ""
                adj = f"{float(r['adjusted_value']):.2f}"
                loss = r['loss_percent']
                
                full_report += (
                    f"ID {rec_id}. Amount: {amt} UAH | Date: {date}{comment}\n"
                    f"   -> Adjusted: {adj} UAH | Loss: {loss}%\n"
                )
                
            if len(full_report) > 2000:
                short_report += f"\nYou have {len(report_data.get('records', []))} records. *Detailed information exceeded the message limit.*"
                await inter.edit_original_response(content=short_report)
            else:
                await inter.edit_original_response(content=full_report)
        except MissingInflationDataError as e:
            await inter.edit_original_response(content=f"Missing data error: {e}")
        except InflationCalculatorError as e:
            await inter.edit_original_response(content=f"Inflation calculator error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in inflation report: {traceback.format_exc()}")
            await inter.edit_original_response(content="An unexpected error occurred while generating the report. Please notify the administrator.")

def setup(bot):
    bot.add_cog(InflationTools(bot))
