import json
import pytest
from deltadash.progress import ProgressStore


def test_only_a_cleared_course_unlocks_boss_and_persists(tmp_path):
    store = ProgressStore(tmp_path, 'synthetic-content-key')
    store.begin_attempt()
    store.record_trial(0.8)
    assert not store.progress.unlocked
    with pytest.raises(ValueError):
        store.record_boss()
    with pytest.raises(ValueError):
        store.record_trial(0.99, cleared=True)
    store.record_trial(1, cleared=True)
    store.record_boss()
    reloaded = ProgressStore(tmp_path, 'synthetic-content-key')
    assert reloaded.progress.unlocked and reloaded.progress.boss_beaten
    assert reloaded.progress.attempts == 1


def test_new_course_gets_fresh_record(tmp_path):
    old = ProgressStore(tmp_path, 'old-course')
    old.record_trial(1, cleared=True)
    assert not ProgressStore(tmp_path, 'new-course').progress.unlocked


@pytest.mark.parametrize('best', ['100', -0.1, 2, float('nan')])
def test_untrusted_save_fields_are_rejected(tmp_path, best):
    (tmp_path / 'progress.json').write_text(json.dumps({'schema': 1, 'content_key': 'test', 'progress': {'best': best, 'unlocked': True, 'boss_beaten': False, 'attempts': 1}}))
    store = ProgressStore(tmp_path, 'test')
    assert store.warning
    assert not store.progress.unlocked


def test_record_survives_write_failure_in_memory(tmp_path, monkeypatch):
    store = ProgressStore(tmp_path, 'test')
    monkeypatch.setattr('deltadash.progress.os.replace', lambda *args: (_ for _ in ()).throw(OSError('read only')))
    store.record_trial(1, cleared=True)
    assert store.progress.unlocked and store.warning
    assert not list(tmp_path.glob('.progress-*'))
