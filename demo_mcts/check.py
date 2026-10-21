"""Demo check for the MCTS transcript: add() must actually add."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from calc import add

assert add(2, 3) == 5, f"add(2, 3) returned {add(2, 3)}, expected 5"
print("PASS: add(2, 3) == 5")
