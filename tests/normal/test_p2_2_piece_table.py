"""
Normal Tests for Phase 2.2: Memoryview Piece Table Buffer Engine & Nano-Checkpoints.
"""

from onyxsi.contextfs.piece_table import PieceTableDocument


def test_piece_table_initialization_and_reading():
    content = "Hello, OnyxSi Piece Table!"
    doc = PieceTableDocument(content)
    assert doc.total_length() == len(content.encode("utf-8"))
    assert doc.get_text() == content
    assert doc.get_slice(0, 5) == b"Hello"
    assert doc.get_slice(7, 6) == b"OnyxSi"


def test_piece_table_insert_and_delete():
    doc = PieceTableDocument("The quick fox jumps")
    # Insert "brown " before "fox" (at index 10)
    doc.insert(10, "brown ")
    assert doc.get_text() == "The quick brown fox jumps"

    # Delete "brown "
    doc.delete(10, 6)
    assert doc.get_text() == "The quick fox jumps"

    # Replace "fox" with "dog"
    doc.replace(10, 3, "dog")
    assert doc.get_text() == "The quick dog jumps"


def test_piece_table_nano_checkpoint_rollback():
    doc = PieceTableDocument("Initial clean code state\n")
    cp1 = doc.create_checkpoint()

    doc.insert(len(doc.get_bytes()), "def corrupted_function(): broken syntax\n")
    assert "corrupted_function" in doc.get_text()

    # Roll back to checkpoint 1
    rolled_back = doc.rollback(cp1)
    assert rolled_back is True
    assert doc.get_text() == "Initial clean code state\n"
    assert "corrupted_function" not in doc.get_text()


def test_piece_table_line_range_extraction():
    multiline = "line 1\nline 2\nline 3\nline 4\nline 5\n"
    doc = PieceTableDocument(multiline)
    extracted = doc.get_line_range(2, 4)
    assert extracted == "line 2\nline 3\nline 4\n"
