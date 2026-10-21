import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))


def pytest_configure(config):
    # One marker per check module; `pytest -k m<N>` then selects that check's scenarios by marker.
    for n in range(1, 17):
        config.addinivalue_line("markers", f"m{n}: scenarios of monitor M{n}")
