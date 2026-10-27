set -euo pipefail
cat <<'EOF'
The flakiness comes from merge_tags() in blog/tags.py, which Post.all_tags calls. It dedupes by
putting the tags in a set and returns list(seen). Set iteration order for strings depends on their
hash, and Python randomizes str hashing per process (PYTHONHASHSEED), so the order of the returned
list changes from run to run. test_post_all_tags asserts an exact ordered list, so it passes or fails
depending on the hash seed of that process.
EOF
