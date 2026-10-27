from datetime import date

from blog.posts import Post, recent
from blog.tags import parse_tags, tag_cloud


def test_parse_tags():
    assert parse_tags("#Python, testing  ci,") == ["python", "testing", "ci"]


def test_post_all_tags():
    post = Post("Fixing CI", date(2024, 4, 2), tags=["Python", "ci"], extra_tags="#ci, pytest")
    assert post.all_tags == ["python", "ci", "pytest"]


def test_tag_cloud_is_ranked():
    posts = [
        Post("a", date(2024, 4, 1), tags=["python"]),
        Post("b", date(2024, 4, 2), tags=["python", "ci"]),
    ]
    assert tag_cloud(posts) == [("python", 2), ("ci", 1)]


def test_recent():
    posts = [Post("old", date(2023, 1, 1)), Post("new", date(2024, 4, 1))]
    assert [p.title for p in recent(posts, today=date(2024, 4, 10))] == ["new"]
