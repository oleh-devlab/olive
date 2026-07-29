import os
import datetime
from modules.inflation_calculator.modules.api import InflationCalculator
import settings

# Define paths for data
DATA_DIR = getattr(settings, "inflation_data_dir", "data/inflation")
RECORDS_DIR = os.path.join(DATA_DIR, "records")
RATES_FILE = os.path.join(DATA_DIR, "inflation_rates.json")

def get_calculator(user_id: int) -> InflationCalculator:
    """
    Returns an instance of InflationCalculator for the specified user_id.
    Ensures that the necessary directories and the rates file exist.
    """
    os.makedirs(RECORDS_DIR, exist_ok=True)
    
    # Initialize empty rates file if it doesn't exist
    if not os.path.exists(RATES_FILE):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(RATES_FILE, "w", encoding="utf-8") as f:
            f.write("{}")

    records_file = os.path.join(RECORDS_DIR, f"{user_id}.json")
    return InflationCalculator.from_json(
        records_filepath=records_file,
        inflation_rates_filepath=RATES_FILE
    )

def add_record(user_id: int, amount: str, date: datetime.date, comment: str = "") -> dict:
    """Adds a new record for the given user."""
    calc = get_calculator(user_id)
    return calc.add_record(amount, date, comment)

def delete_record(user_id: int, record_id: int) -> dict:
    """Deletes a record by ID for the given user."""
    calc = get_calculator(user_id)
    return calc.delete_record(record_id)

def get_report(user_id: int) -> dict:
    """Returns the inflation report for the given user."""
    calc = get_calculator(user_id)
    return calc.get_report()
