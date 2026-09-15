"""Exercise the actual JS paper geometry, not a separate Python approximation."""

import json
import math
import shutil
import subprocess
from itertools import pairwise

import pytest

from photos_mcp.interfaces.http.story_book import BOOK_JS


@pytest.fixture(scope="module")
def paper_frames():
    node = shutil.which("node") or "/opt/homebrew/bin/node"
    if not shutil.which(node):
        pytest.skip("Node is required to execute the browser paper geometry")
    result = subprocess.run(
        [node, "-e", "global.window={};" + BOOK_JS + "\nconsole.log(JSON.stringify([0,.25,.5,.75,1].map(p=>window.PhotosStoryBook.geometry(p,480,32))))"],
        check=True, text=True, capture_output=True,
    )
    return json.loads(result.stdout)


def test_paper_resting_positions_are_flat_and_continuous(paper_frames):
    for progress, frame in ((0, paper_frames[0]), (1, paper_frames[-1])):
        sign = 1 if progress == 0 else -1
        for index, segment in enumerate(frame):
            assert segment["x"] == pytest.approx(sign * index * 15, abs=1e-8)
            assert segment["z"] == pytest.approx(0, abs=1e-8)
            assert segment["angle"] == pytest.approx(progress * math.pi)


def test_paper_bends_instead_of_rotating_as_a_rigid_card(paper_frames):
    middle = paper_frames[2]
    assert len({round(s["angle"], 4) for s in middle}) == 32
    assert max(s["z"] for s in middle) > 350
    assert middle[-1]["angle"] - middle[0]["angle"] > 1.7
    for frame in paper_frames:
        for first, second in pairwise(frame):
            assert math.hypot(second["x"] - first["x"], second["z"] - first["z"]) == pytest.approx(15)


def test_text_never_becomes_injected_html():
    assert ".innerHTML=" not in BOOK_JS
    assert "textContent=text" in BOOK_JS
    assert "layer.inert=true" in BOOK_JS
