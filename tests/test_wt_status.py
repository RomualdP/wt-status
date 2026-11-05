"""End-to-end tests on throw-away git repositories. Run with: python3 -m unittest -v"""
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "wt-status")


def load_module(home):
    os.environ["HOME"] = home
    os.environ["WT_LANG"] = "en"
    loader = importlib.machinery.SourceFileLoader("wt_status", SCRIPT)
    spec = importlib.util.spec_from_loader("wt_status", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def sh(cwd, *cmd):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"{cmd}: {p.stderr}")


def write(path, text="x\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wt-test-")
        self.home = os.path.join(self.tmp, "home")
        self.code = os.path.join(self.home, "code")
        os.makedirs(self.code)
        self.remote = os.path.join(self.tmp, "remote.git")
        sh(self.tmp, "git", "init", "-q", "--bare", self.remote)
        self.repo = os.path.join(self.code, "app")
        sh(self.tmp, "git", "clone", "-q", self.remote, self.repo)
        for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
            sh(self.repo, "git", "config", k, v)
        write(os.path.join(self.repo, "a"))
        sh(self.repo, "git", "add", "a")
        sh(self.repo, "git", "commit", "-qm", "init")
        sh(self.repo, "git", "branch", "-M", "main")
        sh(self.repo, "git", "push", "-q", "origin", "main")
        sh(self.repo, "git", "remote", "set-head", "origin", "main")
        self.wt = load_module(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, rel, branch=None, detach=False):
        path = os.path.join(self.repo, rel) if not os.path.isabs(rel) else rel
        args = ["git", "worktree", "add", "-q"]
        args += ["--detach", path] if detach else ["-b", branch, path]
        sh(self.repo, *args)
        return path

    def commit(self, path, name, push=False):
        write(os.path.join(path, name))
        sh(path, "git", "add", name)
        sh(path, "git", "commit", "-qm", name)
        if push:
            sh(path, "git", "push", "-q", "-u", "origin", "HEAD")

    def rows(self):
        return {os.path.basename(r["path"]): r for r in self.wt.scan([self.code])}


class TestStates(Base):
    def test_classification(self):
        self.add(".claude/worktrees/empty", "empty")
        d = self.add(".claude/worktrees/dirty", "dirty")
        write(os.path.join(d, "new"))
        loc = self.add(os.path.join(self.code, "app-worktrees", "local"), "local")
        self.commit(loc, "l")
        op = self.add(".claude/worktrees/open", "open")
        self.commit(op, "o", push=True)
        mg = self.add(".claude/worktrees/merged", "merged")
        self.commit(mg, "m", push=True)
        sh(self.repo, "git", "merge", "-q", "merged")
        sh(self.repo, "git", "push", "-q", "origin", "main")
        gone = self.add(".claude/worktrees/gone", "gone")
        self.commit(gone, "g", push=True)
        sh(self.repo, "git", "push", "-q", "origin", "--delete", "gone")
        sh(self.repo, "git", "fetch", "-q", "--prune")
        miss = self.add(".claude/worktrees/miss", "miss")
        shutil.rmtree(miss)

        rows = self.rows()
        expected = {"empty": "empty", "dirty": "dirty", "local": "unpushed", "open": "open",
                    "merged": "merged", "gone": "gone", "miss": "missing"}
        self.assertEqual({k: rows[k]["state"] for k in expected}, expected)
        self.assertEqual(rows["dirty"]["origin"], "claude")
        self.assertEqual(rows["local"]["origin"], "other")
        self.assertEqual(rows["dirty"]["dirty"], 1)

    def test_status_failure_is_unknown_not_clean(self):
        p = self.add(".claude/worktrees/broken", "broken")
        with open(os.path.join(p, ".git"), "w") as f:
            f.write("gitdir: /nonexistent\n")
        row = self.rows()["broken"]
        self.assertEqual(row["state"], "unknown")
        self.assertFalse(self.wt.removable(row))
        ok, _ = self.wt.remove_worktree(row)
        self.assertFalse(ok)
        self.assertTrue(os.path.isdir(p))

    def test_origin_detection(self):
        self.assertEqual(self.wt.origin_of("/x/.codex/worktrees/ab/app"), "codex")
        self.assertEqual(self.wt.origin_of("/x/.cursor/worktrees/app/1"), "cursor")
        self.assertEqual(self.wt.origin_of("/x/orca/workspaces/app/feat"), "orca")


class TestRemoval(Base):
    def test_plain_remove_refuses_dirty(self):
        d = self.add(".claude/worktrees/dirty", "dirty")
        write(os.path.join(d, "new"))
        ok, msg = self.wt.remove_worktree(self.rows()["dirty"])
        self.assertFalse(ok)
        self.assertTrue(os.path.isdir(d))

    def test_plain_remove_clean(self):
        p = self.add(".claude/worktrees/empty", "empty")
        ok, _ = self.wt.remove_worktree(self.rows()["empty"])
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(p))
        self.assertIn("empty", subprocess.run(["git", "branch"], cwd=self.repo, capture_output=True,
                                              text=True).stdout)  # branch kept

    def test_force_stashes_changes(self):
        d = self.add(".claude/worktrees/dirty", "dirty")
        write(os.path.join(d, "new"), "precious\n")
        write(os.path.join(d, "a"), "changed\n")
        ok, msg = self.wt.remove_worktree(self.rows()["dirty"], force=True)
        self.assertTrue(ok, msg)
        self.assertFalse(os.path.exists(d))
        stash = subprocess.run(["git", "stash", "list"], cwd=self.repo, capture_output=True, text=True).stdout
        self.assertIn("wt-abandon app/dirty", stash)
        shown = subprocess.run(["git", "show", "stash@{0}^3:new"], cwd=self.repo,
                               capture_output=True, text=True).stdout
        self.assertEqual(shown, "precious\n")

    def test_force_detached_keeps_commits_on_unique_branch(self):
        sh(self.repo, "git", "branch", "wt-backup/det")      # name already taken
        d = self.add(".claude/worktrees/det", detach=True)
        self.commit(d, "work")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d, capture_output=True, text=True).stdout.strip()
        sh(self.repo, "git", "worktree", "lock", d)
        ok, msg = self.wt.remove_worktree(self.rows()["det"], force=True)
        self.assertTrue(ok, msg)
        self.assertIn("wt-backup/det-2", msg)
        kept = subprocess.run(["git", "rev-parse", "wt-backup/det-2"], cwd=self.repo,
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(kept, head)

    def test_missing_is_pruned(self):
        p = self.add(".claude/worktrees/miss", "miss")
        shutil.rmtree(p)
        ok, _ = self.wt.remove_worktree(self.rows()["miss"])
        self.assertTrue(ok)
        self.assertNotIn("miss", self.rows())


class TestDiscovery(Base):
    def test_bare_repo_layout(self):
        bare_root = os.path.join(self.code, "lib")
        bare = os.path.join(bare_root, ".bare")
        sh(self.tmp, "git", "clone", "-q", "--bare", self.remote, bare)
        sh(bare, "git", "worktree", "add", "-q", "-b", "feat", os.path.join(bare_root, "feat"), "main")
        rows = [r for r in self.wt.scan([self.code]) if r["repo"] == "lib"]
        self.assertEqual([r["branch"] for r in rows], ["feat"])

    def test_codex_orphans(self):
        d = os.path.join(self.home, ".codex", "worktrees", "ab12")
        write(os.path.join(d, ".codex-worktree-name"), "")
        full = os.path.join(self.home, ".codex", "worktrees", "cd34")
        write(os.path.join(full, "app", "file"))
        self.assertEqual(self.wt.orphan_codex_dirs(), [d])
        self.assertTrue(self.wt.remove_empty_codex_dir(d))
        self.assertFalse(self.wt.remove_empty_codex_dir(full))
        self.assertTrue(os.path.isdir(full))

    def test_short_path(self):
        self.assertEqual(self.wt.short(self.home + "/code/x", 80), "~/code/x")
        self.assertEqual(self.wt.short(self.home + "x/code", 200), self.home + "x/code")


class TestCli(Base):
    def test_clean_yes(self):
        p = self.add(".claude/worktrees/empty", "empty")
        d = self.add(".claude/worktrees/dirty", "dirty")
        write(os.path.join(d, "new"))
        env = dict(os.environ, HOME=self.home, WT_LANG="en", WT_ROOTS=self.code)
        out = subprocess.run([SCRIPT, "--clean", "--yes"], env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(os.path.exists(p))
        self.assertTrue(os.path.isdir(d))
        self.assertIn("1 worktrees removed", out.stdout)

    def test_french(self):
        self.add(".claude/worktrees/empty", "empty")
        env = dict(os.environ, HOME=self.home, WT_LANG="fr", WT_ROOTS=self.code)
        out = subprocess.run([SCRIPT], env=env, capture_output=True, text=True)
        self.assertIn("supprimable", out.stdout)


if __name__ == "__main__":
    unittest.main()
