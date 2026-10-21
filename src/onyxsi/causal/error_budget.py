"""
3-Strike Error Budget & Mandatory Reflection Gate.
Locks all code mutation (vfs_mutate) after 3 consecutive failures and forces
the agent through reflection_gate with a genuinely new strategy before resetting budget.
"""

from typing import List, Set

MAX_ERROR_BUDGET = 3


class ErrorBudgetError(Exception):
    pass


class MutateLockedByBudgetError(ErrorBudgetError):
    pass


class InvalidReflectionError(ErrorBudgetError):
    pass


class DuplicateStrategyError(ErrorBudgetError):
    pass


class ErrorBudgetTracker:
    """
    Self-regulated 3-strike error budget controller.
    Stops blind retry loops by locking mutations when budget hits 0.
    """

    def __init__(self, max_budget: int = MAX_ERROR_BUDGET):
        self.max_budget = max_budget
        self.remaining_budget = max_budget
        self.is_locked = False
        self.failure_history: List[str] = []
        self._tried_strategies: Set[str] = set()

    def record_strategy_attempt(self, strategy: str) -> None:
        norm = self._normalize(strategy)
        if norm:
            self._tried_strategies.add(norm)

    def record_failure(self, error_summary: str, attempted_strategy: str = "") -> int:
        """Decrements error budget. Locks mutation when budget reaches 0."""
        self.failure_history.append(error_summary)
        if attempted_strategy:
            self._tried_strategies.add(self._normalize(attempted_strategy))

        if self.remaining_budget > 0:
            self.remaining_budget -= 1

        if self.remaining_budget == 0:
            self.is_locked = True

        return self.remaining_budget

    def check_mutate_allowed(self) -> None:
        """Raises MutateLockedByBudgetError if error budget is exhausted."""
        if self.is_locked or self.remaining_budget <= 0:
            raise MutateLockedByBudgetError(
                "Error budget exhausted (0/3 remaining). Call reflection_gate() with root cause and new strategy."
            )

    def reflection_gate(self, root_cause_analysis: str, new_strategy: str) -> int:
        """
        Validates root cause reflection and ensures new_strategy is not a repeat of failed attempts.
        Resets budget to max_budget on approval.
        """
        clean_cause = root_cause_analysis.strip()
        clean_strat = new_strategy.strip()

        if len(clean_cause) < 10 or len(clean_strat) < 10:
            raise InvalidReflectionError("Reflection root cause and new strategy must be substantive (>=10 chars)")

        norm_strat = self._normalize(clean_strat)
        if norm_strat in self._tried_strategies:
            raise DuplicateStrategyError(
                f"Rejected duplicate strategy in reflection_gate: '{clean_strat}' was already attempted"
            )

        self._tried_strategies.add(norm_strat)
        self.remaining_budget = self.max_budget
        self.is_locked = False
        return self.remaining_budget

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.lower().split())
