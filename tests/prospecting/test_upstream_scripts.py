from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = "https://github.com/Kappaemme-git/codex-first-customer-finder-skill.git"


def run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)


class UpstreamScriptTests(unittest.TestCase):
    def make_repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        base = Path(temporary.name)
        upstream_bare = base / "upstream.git"
        fork_bare = base / "fork.git"
        seed = base / "seed"
        work = base / "work"

        self.assertEqual(run(base, "git", "init", "--bare", "--initial-branch=main", str(upstream_bare)).returncode, 0)
        self.assertEqual(run(base, "git", "init", "--bare", "--initial-branch=main", str(fork_bare)).returncode, 0)
        self.assertEqual(run(base, "git", "init", "--initial-branch=main", str(seed)).returncode, 0)
        run(seed, "git", "config", "user.email", "fixture@example.invalid")
        run(seed, "git", "config", "user.name", "Fixture")
        (seed / "first-customer-finder").mkdir()
        (seed / "scripts").mkdir()
        (seed / "logs/execution").mkdir(parents=True)
        (seed / "package.json").write_text('{"name":"codex-first-customer-finder-skill"}\n', encoding="utf-8")
        (seed / ".gitignore").write_text("logs/execution/PROSPECTING_SKILLS_SYNC_LOG.local.md\n", encoding="utf-8")
        (seed / "first-customer-finder/SKILL.md").write_text("fixture\n", encoding="utf-8")
        check_script = (ROOT / "scripts/check-upstream.sh").read_text(encoding="utf-8").replace(EXPECTED, str(upstream_bare))
        (seed / "scripts/check-upstream.sh").write_text(check_script, encoding="utf-8")
        shutil.copy(ROOT / "scripts/sync-upstream.sh", seed / "scripts/sync-upstream.sh")
        shutil.copy(ROOT / "logs/execution/PROSPECTING_SKILLS_EXECUTION_LOG.md", seed / "logs/execution/PROSPECTING_SKILLS_EXECUTION_LOG.md")
        (seed / "conflict.txt").write_text("base\n", encoding="utf-8")
        run(seed, "git", "add", ".")
        self.assertEqual(run(seed, "git", "commit", "-m", "seed").returncode, 0)
        run(seed, "git", "remote", "add", "upstream-local", str(upstream_bare))
        self.assertEqual(run(seed, "git", "push", "upstream-local", "main").returncode, 0)
        self.assertEqual(run(base, "git", "clone", str(upstream_bare), str(work)).returncode, 0)
        run(work, "git", "config", "user.email", "fixture@example.invalid")
        run(work, "git", "config", "user.name", "Fixture")
        run(work, "git", "remote", "set-url", "origin", str(fork_bare))
        run(work, "git", "remote", "add", "upstream", str(upstream_bare))
        self.assertEqual(run(work, "git", "switch", "-c", "unreal").returncode, 0)
        return temporary, work, seed, upstream_bare

    def test_missing_and_wrong_upstream(self) -> None:
        temporary, work, _, _ = self.make_repo()
        with temporary:
            run(work, "git", "remote", "remove", "upstream")
            missing = run(work, "bash", "scripts/check-upstream.sh")
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("upstream remote is missing", missing.stderr)
            run(work, "git", "remote", "add", "upstream", "https://example.invalid/wrong.git")
            wrong = run(work, "bash", "scripts/check-upstream.sh")
            self.assertNotEqual(wrong.returncode, 0)
            self.assertIn("does not match", wrong.stderr)

    def test_url_rewrite_is_rejected(self) -> None:
        temporary, work, _, upstream_bare = self.make_repo()
        with temporary:
            redirected = upstream_bare.parent / "redirected.git"
            self.assertEqual(run(upstream_bare.parent, "git", "init", "--bare", str(redirected)).returncode, 0)
            run(work, "git", "config", f"url.file://{redirected}.insteadOf", str(upstream_bare))
            result = run(work, "bash", "scripts/check-upstream.sh")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("URL rewriting", result.stderr)

    def test_dirty_and_interrupted_worktrees(self) -> None:
        temporary, work, _, _ = self.make_repo()
        with temporary:
            (work / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            dirty = run(work, "bash", "scripts/sync-upstream.sh")
            self.assertNotEqual(dirty.returncode, 0)
            self.assertIn("worktree is dirty", dirty.stderr)
            (work / "dirty.txt").unlink()
            (work / ".git/MERGE_HEAD").write_text("fixture\n", encoding="utf-8")
            interrupted = run(work, "bash", "scripts/sync-upstream.sh")
            self.assertNotEqual(interrupted.returncode, 0)
            self.assertIn("interrupted git operation", interrupted.stderr)

    def test_already_synchronized_no_push_or_remote_mutation(self) -> None:
        temporary, work, _, _ = self.make_repo()
        with temporary:
            remotes_before = run(work, "git", "remote", "-v").stdout
            origin_before = run(work, "git", "ls-remote", "origin").stdout
            head_before = run(work, "git", "rev-parse", "HEAD").stdout
            result = run(work, "bash", "scripts/sync-upstream.sh")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("already synchronized", result.stdout)
            self.assertEqual(remotes_before, run(work, "git", "remote", "-v").stdout)
            self.assertEqual(origin_before, run(work, "git", "ls-remote", "origin").stdout)
            self.assertEqual(head_before, run(work, "git", "rev-parse", "HEAD").stdout)
            self.assertEqual(run(work, "git", "status", "--porcelain").stdout, "")
            repeated = run(work, "bash", "scripts/sync-upstream.sh")
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(run(work, "git", "status", "--porcelain").stdout, "")
            self.assertTrue((work / "logs/execution/PROSPECTING_SKILLS_SYNC_LOG.local.md").is_file())

    def test_upstream_fast_forward(self) -> None:
        temporary, work, seed, _ = self.make_repo()
        with temporary:
            (seed / "upstream-change.txt").write_text("new\n", encoding="utf-8")
            run(seed, "git", "add", "upstream-change.txt")
            run(seed, "git", "commit", "-m", "upstream change")
            self.assertEqual(run(seed, "git", "push", "upstream-local", "main").returncode, 0)
            result = run(work, "bash", "scripts/sync-upstream.sh")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((work / "upstream-change.txt").is_file())
            self.assertEqual(run(work, "git", "merge-base", "--is-ancestor", "main", "unreal").returncode, 0)

    def test_merge_conflict_is_aborted(self) -> None:
        temporary, work, seed, _ = self.make_repo()
        with temporary:
            (work / "conflict.txt").write_text("unreal\n", encoding="utf-8")
            run(work, "git", "add", "conflict.txt")
            run(work, "git", "commit", "-m", "unreal conflict")
            (seed / "conflict.txt").write_text("upstream\n", encoding="utf-8")
            run(seed, "git", "add", "conflict.txt")
            run(seed, "git", "commit", "-m", "upstream conflict")
            run(seed, "git", "push", "upstream-local", "main")
            result = run(work, "bash", "scripts/sync-upstream.sh")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("merge was aborted", result.stderr)
            self.assertEqual(run(work, "git", "branch", "--show-current").stdout.strip(), "unreal")
            self.assertFalse((work / ".git/MERGE_HEAD").exists())
            self.assertEqual(run(work, "git", "status", "--porcelain").stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
