"""Smoke tests for bridge.py logic that doesn't need hardware.
Run on Mac before deploying to Pi:
    python3 test_logic.py

Skips picamera2 imports — those only load at camera capture time.
"""
import os
import sys

# Stub out env so bridge.py can import
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy-for-import")

# Stub out anthropic so we don't actually create a client
class _StubAnthropic:
    def __init__(self, **kw): pass
sys.modules["anthropic"] = type(sys)("anthropic")
sys.modules["anthropic"].Anthropic = _StubAnthropic

import bridge  # noqa: E402


def t(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {got!r} want {want!r}")
    return ok


def main():
    fails = 0

    print("== eval_local ==")
    cases = [
        ("2+2", "4"),
        ("2*3+4", "10"),
        ("2^3", "8"),
        ("2^3^2", "512"),     # right-associative
        ("(2^3)^2", "64"),
        ("sqrt(16)", "4"),
        ("sin(0)", "0"),
        ("pi*2", "6.283185307"),
        ("1/3", "0.3333333333"),
        ("hello", "ERR"),
        ("", "ERR"),
    ]
    for expr, want in cases:
        got = bridge.eval_local(expr)
        if not t(f"eval({expr!r})", got, want): fails += 1

    print()
    print("== ascii_only ==")
    ascii_cases = [
        ("sin(θ_r) ≥ n₂/n₁", "sin(theta_r) >= n_2/n_1"),
        ("x² + 2x = 4, x = -1 ± √(5)", "x^2 + 2x = 4, x = -1 +/- sqrt(5)"),
        ("∫₀^∞ e^(-x²) dx", "integral_0^inf e^(-x^2) dx"),
        ("φ = 45°, n = 1.52", "phi = 45deg, n = 1.52"),
        ("plain ASCII", "plain ASCII"),
    ]
    for inp, want in ascii_cases:
        got = bridge.ascii_only(inp)
        if not t(f"ascii({inp!r})", got, want): fails += 1

    print()
    print("== wrap_lines (26 cols) ==")
    wrap_cases = [
        ("short", ["short"]),
        ("a"*30, ["a"*26, "a"*4]),  # no spaces, hard cut at 26
        ("hello world this is a test of word wrapping", None),  # check length, not exact
    ]
    for inp, want in wrap_cases:
        got = bridge.wrap_lines(inp)
        if want is None:
            ok = all(len(line) <= 26 for line in got)
            print(f"  [{'PASS' if ok else 'FAIL'}] wrap({inp[:30]!r}...): all lines <= 26 -> {ok}")
            if not ok: fails += 1
        else:
            if not t(f"wrap({inp!r})", got, want): fails += 1

    print()
    print(f"== Result: {fails} failures ==")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
