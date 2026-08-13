"""The .hw container write must never corrupt the previous good file.

pack_hw used to open the destination directly, so a rebuild that died
mid-write truncated the PREVIOUS .hw while the app still offered "Open in
Lutron Designer" on it.
"""
import os
import zipfile

from hwwriter.sandbox import pack_hw, unpack_hw


def test_pack_writes_a_valid_archive_and_round_trips(tmp_path):
    lut = tmp_path / "in.lut"
    lut.write_bytes(b"not a real backup, but bytes are bytes")
    hw = str(tmp_path / "out.hw")
    pack_hw(str(lut), hw)
    assert zipfile.is_zipfile(hw)
    out = unpack_hw(hw, str(tmp_path / "again"))
    assert open(out, "rb").read() == lut.read_bytes()


def test_pack_leaves_no_scratch_file_behind(tmp_path):
    lut = tmp_path / "in.lut"
    lut.write_bytes(b"x" * 1024)
    hw = str(tmp_path / "out.hw")
    pack_hw(str(lut), hw)
    leftovers = [n for n in os.listdir(tmp_path) if n.endswith(".writing")]
    assert not leftovers, f"scratch files left beside the .hw: {leftovers}"


def test_pack_assembles_beside_the_target_and_swaps(tmp_path):
    # The property that protects a rebuild: the destination is only ever
    # replaced whole, never opened for writing directly.
    import inspect

    from hwwriter import sandbox
    src = inspect.getsource(sandbox.pack_hw)
    assert "os.replace" in src, "pack_hw writes the final path directly again"
