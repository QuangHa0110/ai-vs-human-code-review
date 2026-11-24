from c3.diffutil import changed_lines, filter_java


JAVA = """\
diff --git a/src/Example.java b/src/Example.java
--- a/src/Example.java
+++ b/src/Example.java
@@ -1 +1 @@
-old
+new
"""

PYTHON = """\
diff --git a/example.py b/example.py
--- a/example.py
+++ b/example.py
@@ -1 +1 @@
-old
+new
"""


def test_filter_java_keeps_only_java_blocks():
    assert filter_java(PYTHON + JAVA + PYTHON) == JAVA
    assert filter_java(PYTHON) == ""


def test_filter_java_keeps_deleted_and_renamed_java_files():
    deleted = """\
diff --git "a/src/\\303\\251.java" "b/src/\\303\\251.java"
deleted file mode 100644
--- "a/src/\\303\\251.java"
+++ /dev/null
@@ -1 +0,0 @@
-class Example {}
"""
    renamed = """\
diff --git a/src/Old Name.txt "b/src/\\303\\251.java"
similarity index 100%
rename from src/Old Name.txt
rename to "src/\\303\\251.java"
"""
    renamed_to_text = """\
diff --git a/src/Old.java b/src/New.txt
similarity index 100%
rename from src/Old.java
rename to src/New.txt
"""
    assert filter_java(deleted + renamed_to_text + renamed) == deleted + renamed


def test_filter_java_handles_unified_headers_without_git_metadata():
    java = JAVA.split("\n", 1)[1]
    python = PYTHON.split("\n", 1)[1]
    assert filter_java(python + java) == java


def test_changed_lines_ignores_headers_and_counts_all_hunks():
    diff = JAVA + """\
@@ -3,3 +3,4 @@
 same
--- removed
+++ added
+extra
 still the same
\\ No newline at end of file
"""
    assert changed_lines(diff + PYTHON) == 7
    assert changed_lines(JAVA.split("@@", 1)[0]) == 0


def test_filter_java_keeps_mode_change_for_quoted_java_path():
    java = """\
diff --git "a/src/\\303\\251.java" "b/src/\\303\\251.java"
old mode 100644
new mode 100755
"""
    assert filter_java(java) == java


def test_filter_java_ignores_header_like_lines_inside_hunks():
    java = """\
--- a/Example.java
+++ b/Example.java
@@ -1 +1 @@
--- a/example.py
+++ b/example.py
"""
    assert filter_java(java) == java


def test_changed_lines_does_not_treat_unicode_separator_as_diff_newline():
    diff = """\
--- a/Example.java
+++ b/Example.java
@@ -1,2 +1,2 @@
 context\u2028 still context
-old
+new
"""
    assert changed_lines(diff) == 2
