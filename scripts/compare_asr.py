"""Tencent ASR vs MERaLiON on a folder of recordings, as a Markdown table (D-053).

Usage: python scripts/compare_asr.py <folder>
The folder holds 16 kHz mono WAVs and expected.txt with one "file<TAB>correct text" per line.
Keep it outside the repo; never commit audio. Needs TENCENTCLOUD_* in .env and mlx-meralion.
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.intent import asr  # noqa: E402


def wer(expected: str, actual: str) -> float:
    """Word error rate: word-level edit distance over the expected word count, with
    spoken numbers normalised to digits first (asr.normalised)."""
    ref, hyp = asr.normalised(expected), asr.normalised(actual)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        current = [i]
        for j, h in enumerate(hyp, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h)))
        previous = current
    return previous[-1] / len(ref)


async def _run(engine: str, data: bytes) -> tuple[str, float]:
    start = time.monotonic()
    try:
        call = asr._tencent("u_demo", data) if engine == "tencent" else asr._meralion(data)
        text = (await call)["transcript"]
    except asr.AsrError as error:
        text = f"ERROR {error.status}"
    return text, time.monotonic() - start


async def main(folder: Path) -> None:
    rows = []
    for line in (folder / "expected.txt").read_text().splitlines():
        name, _, expected = line.partition("\t")
        if not name.strip():
            continue
        data = (folder / name.strip()).read_bytes()
        (t_text, t_sec), (m_text, m_sec) = [await _run(e, data) for e in ("tencent", "meralion")]
        rows.append(
            (
                name,
                expected,
                t_text,
                wer(expected, t_text),
                t_sec,
                m_text,
                wer(expected, m_text),
                m_sec,
            )
        )
    print("| file | expected | Tencent | WER | s | MERaLiON | WER | s |")
    print("|---|---|---|---|---|---|---|---|")
    for name, exp, tt, tw, ts, mt, mw, ms in rows:
        print(f"| {name} | {exp} | {tt} | {tw:.2f} | {ts:.1f} | {mt} | {mw:.2f} | {ms:.1f} |")
    n = len(rows) or 1
    avg = [sum(r[i] for r in rows) / n for i in (3, 4, 6, 7)]
    print(f"| **mean** | | | {avg[0]:.2f} | {avg[1]:.1f} | | {avg[2]:.2f} | {avg[3]:.1f} |")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
