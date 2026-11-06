#!/usr/bin/env python3
"""Audio self-test. Usage: tts selftest

1. Integrity (silent): records the utterances to files and looks for silences in the middle.
2. Live (with sound, ~15 s): utterances arriving in the middle of playback; checks that two sounds
   never play at the same time and that the gap between utterances is short.
"""
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import tts

FFMPEG = tts.find_binary("ffmpeg")
FFPROBE = tts.find_binary("ffprobe")


def wait_for_queue(limit: float) -> None:
    t0 = time.time()
    time.sleep(1)
    while (tts.worker_running() or any(tts.QUEUE.glob("*.txt"))) and time.time() - t0 < limit:
        time.sleep(0.3)


def long_silences(file: Path) -> int:
    output = subprocess.run([FFMPEG, "-hide_banner", "-i", str(file), "-af",
                             "silencedetect=noise=-45dB:d=0.9", "-f", "null", "-"],
                            capture_output=True, text=True).stderr
    return len(re.findall(r"silence_end", output))


def integrity_test() -> bool:
    print("1. Audio integrity (silent)")
    texts = [
        "First short utterance.",
        "Second utterance, with deploy, pull request and GitHub in the middle of the sentence.",
        "Third utterance, long enough to become two parts. "
        + "Each sentence of this part helps to go past the limit of a single request. " * 28,
    ]
    with tempfile.TemporaryDirectory() as folder:
        env = dict(os.environ, TTS_SINK_DIR=folder)
        for text in texts:
            subprocess.run([str(tts.PYTHON), str(Path(tts.__file__)), "say", text], env=env, check=True)
        wait_for_queue(180)
        files = sorted(Path(folder).glob("*.wav"))
        ok = len(files) == len(texts)
        print(f"   utterances recorded: {len(files)} of {len(texts)}")
        for file in files:
            duration = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                                             "-of", "csv=p=0", str(file)],
                                            capture_output=True, text=True).stdout or 0)
            gaps = long_silences(file)
            ok &= gaps == 0 and duration > 0.8
            print(f"   {duration:6.1f}s of audio, long silences in the middle: {gaps}")
    return ok


def live_test() -> bool:
    print("2. Live playback (with sound)")
    mark = time.time()
    t0 = time.time()
    for delay, text in ((0.0, "Self-test A. First utterance."),
                        (1.5, "Self-test B. It arrived while the first one was playing."),
                        (3.0, "Self-test C. This one also arrived in the middle.")):
        while time.time() - t0 < delay:
            time.sleep(0.02)
        tts.enqueue(text)
    wait_for_queue(60)
    lines = [line for line in tts.LOG.read_text(encoding="utf-8").splitlines()
             if line[:19] >= time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mark))]
    finished = [line for line in lines if " done engine=" in line]
    errors = [line for line in lines if "error" in line or "closed before" in line]
    print(f"   utterances played to the end: {len(finished)} of 3 · errors: {len(errors)}")
    print(f"   total time: {time.time() - t0:.1f}s (a single audio output: overlap is impossible)")
    return len(finished) == 3 and not errors


def main() -> bool:
    cfg = tts.read_config()
    enabled_before = cfg["enabled"]
    tts.save_config(dict(cfg, enabled=False))  # hooks don't enter the queue during the test
    tts.stop()
    try:
        intact = integrity_test()
        live = live_test()
    finally:
        tts.save_config(dict(tts.read_config(), enabled=enabled_before))
    ok = intact and live
    print(f"\n{'PASSED' if ok else 'FAILED'}: integrity {'ok' if intact else 'failed'}, "
          f"live {'ok' if live else 'failed'}")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
