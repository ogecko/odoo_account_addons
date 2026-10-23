"""The pre-commit hook blocks business data from being committed."""
import os
import shutil
import subprocess

import pytest

HOOK = os.path.join(os.path.dirname(__file__), '..', '.githooks', 'pre-commit')


def git(repo, *args, **kw):
    return subprocess.run(['git', '-C', str(repo)] + list(args), capture_output=True, text=True, **kw)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, 'init', '-q', '-b', 'main')
    git(tmp_path, 'config', 'user.email', 't@example.com')
    git(tmp_path, 'config', 'user.name', 'T')
    hooks = tmp_path / '.githooks'
    hooks.mkdir()
    shutil.copy(HOOK, hooks / 'pre-commit')
    git(tmp_path, 'config', 'core.hooksPath', '.githooks')
    (tmp_path / '.gitignore').write_text('data/\nsecrets/\n.private-terms\n')
    (tmp_path / '.private-terms').write_text('# private names\nmegacorp\n\\bzzq\\b\n')
    (tmp_path / 'data' / 'examples').mkdir(parents=True)
    (tmp_path / 'data' / 'examples' / 'x.ofx').write_text(
        '<STMTTRN><TRNAMT>-1234.56<MEMO>Something</STMTTRN>'
        '<STMTTRN><TRNAMT>0.11<MEMO>Fee</STMTTRN>')
    return tmp_path


def commit(repo, name, content, force=False):
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content)
    git(repo, 'add', *(['-f', name] if force else [name]))
    return git(repo, 'commit', '-q', '-m', 'test')


def test_clean_file_commits(repo):
    assert commit(repo, 'ok.py', 'x = 44.00\n').returncode == 0


def test_blocks_private_term_case_insensitive(repo):
    r = commit(repo, 'a.py', "name = 'MegaCorp Pty'\n")
    assert r.returncode != 0
    assert 'megacorp' in r.stderr.lower()


def test_blocks_private_term_regex_word(repo):
    assert commit(repo, 'a.py', "x = 'ZZQ services'\n").returncode != 0
    git(repo, 'rm', '-q', '--cached', 'a.py')
    assert commit(repo, 'b.py', "x = 'website_zzqx'\n").returncode == 0


def test_blocks_amount_from_ofx_examples(repo):
    r = commit(repo, 'a.py', 'amount = 1234.56\n')
    assert r.returncode != 0
    assert '1234.56' in r.stderr


def test_small_ofx_amounts_not_blocked(repo):
    # Amounts under 10.00 are too common to be meaningful.
    assert commit(repo, 'a.py', 'fee = 0.11\n').returncode == 0


def test_blocks_ignored_path_even_if_forced(repo):
    r = commit(repo, 'secrets/pw', 'x\n', force=True)
    assert r.returncode != 0
    assert 'ignored' in r.stderr


def test_blocks_data_file_types(repo):
    for name in ('db.dump', 'x.ofx', 'a.tgz', 'b.tar.gz', 'c.sql', 'd.zip', 'e.ofx:Zone.Identifier'):
        r = commit(repo, name, 'x\n')
        assert r.returncode != 0, name
        git(repo, 'reset', '-q')


def test_blocks_large_file(repo):
    r = commit(repo, 'big.bin', b'0' * (1024 * 1024 + 1))
    assert r.returncode != 0
    assert '1 MB' in r.stderr


def test_works_without_terms_or_examples(repo):
    os.remove(repo / '.private-terms')
    shutil.rmtree(repo / 'data')
    assert commit(repo, 'ok.py', 'x = 1\n').returncode == 0
