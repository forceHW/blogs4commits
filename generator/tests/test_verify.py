from blog_generator.verify import check_highlight, new_side

PATCH = """@@ -1,4 +1,5 @@
 def handler(event):
-    return None
+    table = get_table()
+    return table.query(status="published")
 # end
@@ -20,2 +21,3 @@ class X:
     pass
+    extra = 1
"""

FILES = [{"filename": "app.py", "status": "modified", "patch": PATCH}, {"filename": "logo.png", "status": "added"}]


def test_new_side_excludes_removed_lines_and_splits_hunks():
    hunks = new_side(PATCH)
    assert hunks[0] == [
        "def handler(event):",
        "    table = get_table()",
        '    return table.query(status="published")',
        "# end",
    ]
    assert hunks[1][:2] == ["    pass", "    extra = 1"]


def test_added_lines_match_with_trailing_space_and_crlf_differences():
    code = '    table = get_table()\r\n    return table.query(status="published")\n'
    assert check_highlight("app.py", code, FILES) is None


def test_block_with_context_lines_matches():
    code = 'def handler(event):\n    table = get_table()'
    assert check_highlight("app.py", code, FILES) is None


def test_removed_line_is_rejected():
    assert check_highlight("app.py", "    return None", FILES) is not None


def test_block_across_hunks_is_rejected():
    assert check_highlight("app.py", "# end\n    pass", FILES) is not None


def test_altered_code_is_rejected():
    assert check_highlight("app.py", "    table = get_table(1)", FILES) is not None


def test_wrong_file_and_binary_file_are_rejected():
    assert "not changed" in check_highlight("other.py", "x", FILES)
    assert "no text diff" in check_highlight("logo.png", "x", FILES)
