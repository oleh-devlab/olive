import logging
from dataclasses import dataclass

from core.database import db

logger = logging.getLogger(__name__)


@dataclass
class LLMTokenBudget:
    context_tokens: int = 64000
    reserved_system_tokens: int = 6000
    reserved_memory_tokens: int = 32000
    reserved_response_tokens: int = 5000

    @property
    def total(self) -> int:
        """Total tokens required: dialogue + all reservations."""
        return (
            self.context_tokens
            + self.reserved_system_tokens
            + self.reserved_memory_tokens
            + self.reserved_response_tokens
        )

    def validate(self, min_model_tokens: int) -> str | None:
        """Check if the budget fits within the smallest model's context window.

        Returns None if valid, or an error message string if not.
        """
        if min_model_tokens < self.total:
            return f"Token budget total ({self.total:,}) exceeds the smallest model context window ({min_model_tokens:,}). "
        return None


class BudgetRepository:
    @staticmethod
    def get_by_name(name: str = "default") -> LLMTokenBudget:
        """Load a named budget from the llm_token_budgets table.

        Falls back to defaults if the row doesn't exist.
        """

        rows = db.execute(
            "SELECT context_tokens, reserved_system_tokens, reserved_memory_tokens, reserved_response_tokens "
            "FROM llm_token_budgets WHERE name = ?",
            (name,),
        )
        if rows:
            logger.info("Loaded LLM token budget '%s' from DB", name)
            return LLMTokenBudget(**rows[0])

        logger.warning("Token budget '%s' not found in DB, using defaults.", name)
        return LLMTokenBudget()

    @staticmethod
    def save_to_db(name: str, budget: LLMTokenBudget) -> None:
        """Persist the current budget to the llm_token_budgets table.

        Creates the row if it doesn't exist (upsert).
        """
        if name is None:
            raise ValueError("Cannot save budget to DB: no name assigned (name is None).")

        db.execute(
            "INSERT INTO llm_token_budgets (name, context_tokens, reserved_system_tokens, reserved_memory_tokens, reserved_response_tokens) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "context_tokens=excluded.context_tokens, "
            "reserved_system_tokens=excluded.reserved_system_tokens, "
            "reserved_memory_tokens=excluded.reserved_memory_tokens, "
            "reserved_response_tokens=excluded.reserved_response_tokens",
            (
                name,
                budget.context_tokens,
                budget.reserved_system_tokens,
                budget.reserved_memory_tokens,
                budget.reserved_response_tokens,
            ),
        )
