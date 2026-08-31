"""Production Run Archive Contract.

The Operational Run directory is not durable evidence. It is dirty state: the
next Preflight demands a clean work tree, ``runs/YYYY-MM-DD/`` is git-ignored,
a crashed pipeline leaves it half-written, and a human tidying up can delete
the only copy of what actually happened on a given night.

So these tests pin the properties that make the sealed archive trustworthy,
each of which fails in a way that silently destroys evidence if it regresses:

* the Operational Run is **read-only source** -- archiving never writes to it;
* every archived byte is the source byte, re-hashed from the destination;
* the manifest covers exactly the archived files, no more and no fewer;
* an existing archive is confirmed, never rewritten -- and a source that has
  since diverged is a hard error rather than a silent second version;
* a partially built archive is never visible;
* an INCOMPLETE business run is still archived;
* the historical Source Matrix lands where Selection Calibration reads it;
* nothing in the module reaches the network, git, or a ``--force`` path.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from src import production_archive
from src.contracts import validate_json_document, validate_run_artifact_allowlist
from src.production_archive import (
    ARCHIVE_STATUS_COMPLETE_VERIFIED,
    ARCHIVE_STATUS_INCOMPLETE,
    ARCHIVE_VERSION,
    ARCHIVE_VERSION_V2,
    MANIFEST_NAME,
    MANIFEST_SHA_NAME,
    RESULT_ALREADY_ARCHIVED,
    RESULT_ARCHIVE_VERIFIED,
    RESULT_ARCHIVED,
    RUNTIME_SECURITY_MISSING,
    RUNTIME_SECURITY_VALID,
    ProductionArchiveError,
    archive_main,
    archive_production_run,
    registry_dir,
    scan_tree,
    store_source_matrix_in_registry,
    verify_archive_integrity,
    verify_main,
    verify_production_archive,
)
from src.production_verify import VERIFIED_CASE_C_NO_TRADE, VERIFIED_STATUSES
from src.selection_calibration import resolve_historical_source_matrix_path
from tests.legacy_runtime_security import (
    RUNTIME_SECURITY_CHECKS,
    build_runtime_security_document,
)
from tests.production_run_fixtures import HISTORICAL_SOURCE_MATRIX, build_case_c_run


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent

TARGET_DATE = "2026-08-12"
ISSUER_REGISTRY = PROJECT_ROOT / "config" / "issuer_domain_registry.yaml"


# ------------------------------------------------------------- helpers ---


def _tree(root: Path) -> dict[str, bytes]:
    """Every regular file under ``root`` as ``relative path -> raw bytes``."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _unseal(root: Path) -> None:
    """Make a sealed archive writable again so a test can tamper with it."""
    for directory, _dirnames, filenames in os.walk(root):
        os.chmod(directory, 0o755)
        for name in filenames:
            os.chmod(Path(directory) / name, 0o644)


def _runtime_security_document(target_date: str = TARGET_DATE) -> dict:
    return build_runtime_security_document(
        target_date=target_date,
        platform_name="Linux-test",
        claude_code_version="2.1.219",
        git_head_sha="a" * 40,
        production_python="/usr/bin/python3",
        managed_settings_sha256="b" * 64,
        managed_runtime_guard_sha256="c" * 64,
        source_matrix_path=HISTORICAL_SOURCE_MATRIX,
        issuer_registry_path=ISSUER_REGISTRY,
        allowed_domains=["www.jpx.co.jp"],
        checks={name: "PASS" for name in RUNTIME_SECURITY_CHECKS},
        http_user_agent_present=True,
    )


def _write_runtime_security(run_dir: Path, payload: dict | str) -> Path:
    working = run_dir / "working"
    working.mkdir(parents=True, exist_ok=True)
    path = working / "runtime_security.json"
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return path


def _archive(tmp_path: Path, run_dir: Path, **overrides):
    """Archive ``run_dir`` under a per-test DayTrade root and Archive Root."""
    daytrade_root = run_dir.parent.parent
    kwargs = {
        "daytrade_root": daytrade_root,
        "archive_root": tmp_path / "archive",
        "source_matrix_path": HISTORICAL_SOURCE_MATRIX,
        "issuer_registry_path": ISSUER_REGISTRY,
    }
    kwargs.update(overrides)
    return archive_production_run(TARGET_DATE, **kwargs)


@pytest.fixture()
def run_dir(tmp_path):
    """A complete Case C run, relocated to ``<root>/runs/<target-date>``."""
    built = build_case_c_run(tmp_path / "build", selection_status="NO_TRADE")
    destination = tmp_path / "daytrade" / "runs" / TARGET_DATE
    destination.parent.mkdir(parents=True)
    built.rename(destination)
    return destination


@pytest.fixture()
def archive_root(tmp_path):
    return tmp_path / "archive"


# ------------------------------------------------------------- scanning ---


def test_scan_tree_returns_sorted_relative_posix_paths(tmp_path):
    (tmp_path / "b" / "c").mkdir(parents=True)
    (tmp_path / "b" / "c" / "deep.json").write_text("{}", encoding="utf-8")
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")

    assert scan_tree(tmp_path) == ("a.json", "b/c/deep.json")


def test_scan_tree_refuses_a_symlink_instead_of_following_it(tmp_path):
    (tmp_path / "real.json").write_text("{}", encoding="utf-8")
    (tmp_path / "link.json").symlink_to(tmp_path / "real.json")

    with pytest.raises(ProductionArchiveError) as error:
        scan_tree(tmp_path)
    assert error.value.code == "PRODUCTION_ARCHIVE_SOURCE_UNSAFE_ENTRY"


def test_scan_tree_refuses_a_non_regular_file(tmp_path):
    os.mkfifo(tmp_path / "pipe")

    with pytest.raises(ProductionArchiveError) as error:
        scan_tree(tmp_path)
    assert error.value.code == "PRODUCTION_ARCHIVE_SOURCE_UNSAFE_ENTRY"


def test_scan_tree_refuses_a_file_as_the_tree_root(tmp_path):
    target = tmp_path / "file"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(ProductionArchiveError) as error:
        scan_tree(target)
    assert error.value.code == "PRODUCTION_ARCHIVE_SOURCE_NOT_DIRECTORY"


# -------------------------------------------------------------- archive ---


def test_archiving_a_complete_run_seals_it_verified(tmp_path, run_dir, archive_root):
    """TC-09: a business-VERIFIED run is COMPLETE_VERIFIED, on its own.

    No attestation is written, none is needed, and none appears in the
    manifest: what an archive claims is that this evidence and this decision
    verify, not that some particular local Claude configuration produced them.
    """
    result = _archive(tmp_path, run_dir)

    assert result["result"] == RESULT_ARCHIVED
    assert result["target_date"] == TARGET_DATE
    assert result["archive_status"] == ARCHIVE_STATUS_COMPLETE_VERIFIED

    archive_dir = archive_root / "runs" / TARGET_DATE
    manifest = json.loads((archive_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["archive_version"] == ARCHIVE_VERSION_V2
    assert manifest["schema_version"] == 2
    assert manifest["business_verification"]["status"] == VERIFIED_CASE_C_NO_TRADE
    assert manifest["source"] == {"run_relative_path": f"runs/{TARGET_DATE}"}
    assert "runtime_security" not in manifest


def test_the_operational_run_is_never_modified(tmp_path, run_dir):
    _write_runtime_security(run_dir, _runtime_security_document())
    before = _tree(run_dir)
    before_stat = {
        name: os.stat(run_dir / name).st_mtime_ns for name in before
    }

    _archive(tmp_path, run_dir)

    assert _tree(run_dir) == before
    assert {name: os.stat(run_dir / name).st_mtime_ns for name in before} == before_stat


def test_every_archived_byte_equals_the_source_byte(tmp_path, run_dir, archive_root):
    source = _tree(run_dir)

    _archive(tmp_path, run_dir)

    assert _tree(archive_root / "runs" / TARGET_DATE / "run") == source


def test_the_manifest_is_schema_valid_and_digest_matched(
    tmp_path, run_dir, archive_root
):
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE

    raw = (archive_dir / MANIFEST_NAME).read_bytes()
    manifest = json.loads(raw.decode("utf-8"))
    validate_json_document(manifest, "production_archive_manifest_v2.schema.json")

    digest = (archive_dir / MANIFEST_SHA_NAME).read_bytes()
    assert len(digest) == 65 and digest.endswith(b"\n")
    assert digest[:64].decode("ascii") == production_archive.sha256_bytes(raw)


def test_the_manifest_covers_exactly_the_archived_files(
    tmp_path, run_dir, archive_root
):
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE
    manifest = json.loads((archive_dir / MANIFEST_NAME).read_text(encoding="utf-8"))

    listed = [entry["path"] for entry in manifest["files"]]
    present = [
        f"{subtree}/{relative}"
        for subtree in ("inputs", "run", "verification")
        for relative in scan_tree(archive_dir / subtree)
    ]
    assert sorted(listed) == sorted(present)
    assert listed == sorted(listed)
    assert manifest["total_file_count"] == len(listed)
    assert manifest["total_size_bytes"] == sum(
        entry["size_bytes"] for entry in manifest["files"]
    )
    # the manifest and its digest are deliberately not self-listed
    assert MANIFEST_NAME not in listed
    assert MANIFEST_SHA_NAME not in listed


def test_the_archived_inputs_are_the_in_force_configuration(
    tmp_path, run_dir, archive_root
):
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE

    assert (archive_dir / "inputs/source_matrix.yaml").read_bytes() == (
        HISTORICAL_SOURCE_MATRIX.read_bytes()
    )
    assert (archive_dir / "inputs/issuer_domain_registry.yaml").read_bytes() == (
        ISSUER_REGISTRY.read_bytes()
    )


def test_the_business_verification_report_is_stored_verbatim(
    tmp_path, run_dir, archive_root
):
    _archive(tmp_path, run_dir)
    report = json.loads(
        (archive_root / "runs" / TARGET_DATE / "verification/production_verify.json")
        .read_text(encoding="utf-8")
    )
    assert report["status"] in VERIFIED_STATUSES
    assert report["errors"] == []
    assert report["checks"]


def test_a_finished_archive_is_sealed_read_only(tmp_path, run_dir, archive_root):
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE

    assert stat.S_IMODE(os.stat(archive_dir).st_mode) == 0o555
    for directory, _dirnames, filenames in os.walk(archive_dir):
        assert stat.S_IMODE(os.stat(directory).st_mode) == 0o555, directory
        for name in filenames:
            path = Path(directory) / name
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o444, path


def test_seal_tree_leaves_an_unsealed_root_owner_writable(tmp_path):
    """The seven-point seal contract, exercised directly on a real tree.

    Renaming a directory into a different parent rewrites its ``..`` entry,
    which the kernel refuses (EACCES) on a 0555 directory. So an unsealed root
    must stay owner-writable *while its contents are already sealed*, and only
    the moved-into-place archive root gets 0555.
    """
    staging = tmp_path / "staging"
    (staging / "run" / "working").mkdir(parents=True)
    (staging / "run" / "sources.json").write_text("{}", encoding="utf-8")
    (staging / "run" / "working" / "runtime_security.json").write_text(
        "{}", encoding="utf-8"
    )
    (staging / "manifest.json").write_text("{}", encoding="utf-8")

    production_archive._seal_tree(staging, seal_root=False)

    # 1. the staging root itself is still owner-writable
    assert stat.S_IMODE(os.stat(staging).st_mode) & stat.S_IWUSR
    assert stat.S_IMODE(os.stat(staging).st_mode) != 0o555
    assert os.access(staging, os.W_OK)
    # 2. / 3. everything *inside* it is already sealed
    for relative in (
        "manifest.json",
        "run/sources.json",
        "run/working/runtime_security.json",
    ):
        assert stat.S_IMODE(os.stat(staging / relative).st_mode) == 0o444, relative
    for relative in ("run", "run/working"):
        assert stat.S_IMODE(os.stat(staging / relative).st_mode) == 0o555, relative

    # 4. the move into a *different* parent succeeds
    final = tmp_path / "archive" / "runs" / TARGET_DATE
    final.parent.mkdir(parents=True)
    os.replace(staging, final)
    os.chmod(final, production_archive.SEALED_DIR_MODE)

    # 5. / 6. / 7. the finished archive is fully sealed
    assert stat.S_IMODE(os.stat(final).st_mode) == 0o555
    for directory, _dirnames, filenames in os.walk(final):
        assert stat.S_IMODE(os.stat(directory).st_mode) == 0o555, directory
        for name in filenames:
            path = Path(directory) / name
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o444, path


def test_seal_tree_seals_the_root_when_asked(tmp_path):
    root = tmp_path / "sealed"
    (root / "inner").mkdir(parents=True)
    (root / "inner" / "file.json").write_text("{}", encoding="utf-8")

    production_archive._seal_tree(root)

    assert stat.S_IMODE(os.stat(root).st_mode) == 0o555
    assert stat.S_IMODE(os.stat(root / "inner").st_mode) == 0o555
    assert stat.S_IMODE(os.stat(root / "inner" / "file.json").st_mode) == 0o444


def test_no_staging_directory_survives_a_successful_archive(
    tmp_path, run_dir, archive_root
):
    _archive(tmp_path, run_dir)
    assert list((archive_root / ".staging").iterdir()) == []


# -------------------------------------------- INCOMPLETE runs are kept ---


def test_an_incomplete_run_is_still_archived(tmp_path, archive_root):
    run = tmp_path / "daytrade" / "runs" / TARGET_DATE
    run.mkdir(parents=True)
    (run / "sources.json").write_text("{}", encoding="utf-8")

    result = _archive(tmp_path, run)

    assert result["result"] == RESULT_ARCHIVED
    assert result["archive_status"] == ARCHIVE_STATUS_INCOMPLETE
    archived = archive_root / "runs" / TARGET_DATE
    assert (archived / "run/sources.json").read_text(encoding="utf-8") == "{}"
    report = json.loads(
        (archived / "verification/production_verify.json").read_text(encoding="utf-8")
    )
    assert report["status"] not in VERIFIED_STATUSES


@pytest.mark.parametrize(
    "sidecar",
    [
        pytest.param(None, id="absent"),
        pytest.param("{ not json", id="unparseable"),
        pytest.param("valid", id="v1-attestation"),
    ],
)
def test_a_leftover_attestation_does_not_change_the_archive_status(
    tmp_path, run_dir, archive_root, sidecar
):
    """TC-10 / AC-13: the sidecar has no vote any more.

    A run copied from an older host may still carry a
    ``working/runtime_security.json``. It is archived as raw bytes like every
    other sidecar file, and it changes nothing about what the archive claims.
    """
    if sidecar == "valid":
        _write_runtime_security(run_dir, _runtime_security_document())
    elif sidecar is not None:
        _write_runtime_security(run_dir, sidecar)

    result = _archive(tmp_path, run_dir)

    assert result["archive_status"] == ARCHIVE_STATUS_COMPLETE_VERIFIED
    manifest = json.loads(
        (archive_root / "runs" / TARGET_DATE / MANIFEST_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert manifest["business_verification"]["status"] in VERIFIED_STATUSES
    assert "runtime_security" not in manifest


# ---------------------------------------------------------- idempotency ---


def test_archiving_twice_confirms_instead_of_rewriting(tmp_path, run_dir, archive_root):
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE
    before = {
        path.relative_to(archive_dir).as_posix(): os.stat(path).st_mtime_ns
        for path in archive_dir.rglob("*")
        if path.is_file()
    }

    result = _archive(tmp_path, run_dir)

    assert result["result"] == RESULT_ALREADY_ARCHIVED
    assert result["archive_status"] == ARCHIVE_STATUS_COMPLETE_VERIFIED
    after = {
        path.relative_to(archive_dir).as_posix(): os.stat(path).st_mtime_ns
        for path in archive_dir.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_a_changed_source_file_is_a_hard_error(tmp_path, run_dir, archive_root):
    _archive(tmp_path, run_dir)
    (run_dir / "sources.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ProductionArchiveError) as error:
        _archive(tmp_path, run_dir)
    assert error.value.code == "PRODUCTION_ARCHIVE_SOURCE_DIVERGED"


def test_an_added_source_file_is_a_hard_error(tmp_path, run_dir, archive_root):
    _archive(tmp_path, run_dir)
    (run_dir / "extra.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ProductionArchiveError) as error:
        _archive(tmp_path, run_dir)
    assert error.value.code == "PRODUCTION_ARCHIVE_SOURCE_DIVERGED"


def test_a_removed_source_file_is_a_hard_error(tmp_path, run_dir, archive_root):
    _archive(tmp_path, run_dir)
    (run_dir / "sources.json").unlink()

    with pytest.raises(ProductionArchiveError) as error:
        _archive(tmp_path, run_dir)
    assert error.value.code == "PRODUCTION_ARCHIVE_SOURCE_DIVERGED"


def test_an_existing_but_corrupt_archive_is_never_silently_replaced(
    tmp_path, run_dir, archive_root
):
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE
    _unseal(archive_dir)
    (archive_dir / "run/sources.json").write_text("tampered", encoding="utf-8")

    with pytest.raises(ProductionArchiveError) as error:
        _archive(tmp_path, run_dir)
    assert error.value.code == "PRODUCTION_ARCHIVE_EXISTS_INVALID"
    assert (archive_dir / "run/sources.json").read_text(encoding="utf-8") == "tampered"


def test_a_stale_staging_directory_stops_the_archive(tmp_path, run_dir, archive_root):
    (archive_root / ".staging" / f"{TARGET_DATE}.abc").mkdir(parents=True)

    with pytest.raises(ProductionArchiveError) as error:
        _archive(tmp_path, run_dir)
    assert error.value.code == "PRODUCTION_ARCHIVE_STAGING_EXISTS"
    assert not (archive_root / "runs" / TARGET_DATE).exists()


def test_a_failed_archive_leaves_no_partial_archive(
    tmp_path, run_dir, archive_root, monkeypatch
):
    def _explode(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(production_archive, "_build_manifest", _explode)

    with pytest.raises(RuntimeError, match="disk on fire"):
        _archive(tmp_path, run_dir)

    assert not (archive_root / "runs" / TARGET_DATE).exists()
    assert list((archive_root / ".staging").iterdir()) == []


# ---------------------------------------------------------- bad inputs ---


@pytest.mark.parametrize(
    "value", ["2026-13-01", "2026-02-30", "20260812", "", "../etc", "2026-08-12 "]
)
def test_an_invalid_target_date_is_rejected(tmp_path, archive_root, value):
    with pytest.raises(ProductionArchiveError) as error:
        archive_production_run(
            value,
            daytrade_root=tmp_path / "daytrade",
            archive_root=archive_root,
            source_matrix_path=HISTORICAL_SOURCE_MATRIX,
            issuer_registry_path=ISSUER_REGISTRY,
        )
    assert error.value.code == "PRODUCTION_ARCHIVE_TARGET_DATE_INVALID"


def test_a_missing_run_directory_is_rejected(tmp_path, archive_root):
    (tmp_path / "daytrade" / "runs").mkdir(parents=True)

    with pytest.raises(ProductionArchiveError) as error:
        archive_production_run(
            TARGET_DATE,
            daytrade_root=tmp_path / "daytrade",
            archive_root=archive_root,
            source_matrix_path=HISTORICAL_SOURCE_MATRIX,
            issuer_registry_path=ISSUER_REGISTRY,
        )
    assert error.value.code == "PRODUCTION_ARCHIVE_SOURCE_MISSING"


def test_a_symlinked_run_directory_is_rejected(tmp_path, archive_root):
    runs = tmp_path / "daytrade" / "runs"
    runs.mkdir(parents=True)
    real = tmp_path / "elsewhere"
    real.mkdir()
    (runs / TARGET_DATE).symlink_to(real, target_is_directory=True)

    with pytest.raises(ProductionArchiveError) as error:
        archive_production_run(
            TARGET_DATE,
            daytrade_root=tmp_path / "daytrade",
            archive_root=archive_root,
            source_matrix_path=HISTORICAL_SOURCE_MATRIX,
            issuer_registry_path=ISSUER_REGISTRY,
        )
    assert error.value.code == "PRODUCTION_ARCHIVE_SOURCE_UNSAFE_ENTRY"


def test_a_run_path_that_is_a_file_is_rejected(tmp_path, archive_root):
    runs = tmp_path / "daytrade" / "runs"
    runs.mkdir(parents=True)
    (runs / TARGET_DATE).write_text("not a run", encoding="utf-8")

    with pytest.raises(ProductionArchiveError) as error:
        archive_production_run(
            TARGET_DATE,
            daytrade_root=tmp_path / "daytrade",
            archive_root=archive_root,
            source_matrix_path=HISTORICAL_SOURCE_MATRIX,
            issuer_registry_path=ISSUER_REGISTRY,
        )
    assert error.value.code == "PRODUCTION_ARCHIVE_SOURCE_NOT_DIRECTORY"


def test_a_missing_source_matrix_is_rejected(tmp_path, run_dir, archive_root):
    with pytest.raises(ProductionArchiveError) as error:
        _archive(tmp_path, run_dir, source_matrix_path=tmp_path / "nope.yaml")
    assert error.value.code == "PRODUCTION_ARCHIVE_SOURCE_MISSING"
    assert not (archive_root / "runs" / TARGET_DATE).exists()


def test_a_symlink_inside_the_run_aborts_the_whole_archive(
    tmp_path, run_dir, archive_root
):
    (run_dir / "link.json").symlink_to(run_dir / "sources.json")

    with pytest.raises(ProductionArchiveError) as error:
        _archive(tmp_path, run_dir)
    assert error.value.code == "PRODUCTION_ARCHIVE_SOURCE_UNSAFE_ENTRY"
    assert not (archive_root / "runs" / TARGET_DATE).exists()


# ------------------------------------------------- tamper detection ---


def test_verify_accepts_a_freshly_sealed_archive(tmp_path, run_dir, archive_root):
    _archive(tmp_path, run_dir)

    result = verify_production_archive(TARGET_DATE, archive_root=archive_root)

    assert result["result"] == RESULT_ARCHIVE_VERIFIED
    assert result["schema_version"] == 2
    assert result["archive_status"] == ARCHIVE_STATUS_COMPLETE_VERIFIED
    assert result["stored_business_verification_status"] == VERIFIED_CASE_C_NO_TRADE
    assert result["current_business_reverification_status"] == VERIFIED_CASE_C_NO_TRADE
    assert result["runtime_security_status"] is None
    assert result["source_matrix_registry"] == "PRESENT"
    assert result["total_file_count"] > 0


def test_verify_rejects_a_missing_archive(tmp_path, archive_root):
    with pytest.raises(ProductionArchiveError) as error:
        verify_production_archive(TARGET_DATE, archive_root=archive_root)
    assert error.value.code == "PRODUCTION_ARCHIVE_SOURCE_MISSING"


def test_verify_detects_a_single_flipped_byte(tmp_path, run_dir, archive_root):
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE
    _unseal(archive_dir)
    target = archive_dir / "run/sources.json"
    payload = bytearray(target.read_bytes())
    payload[0] = payload[0] ^ 0x20
    target.write_bytes(bytes(payload))

    with pytest.raises(ProductionArchiveError) as error:
        verify_production_archive(TARGET_DATE, archive_root=archive_root)
    assert error.value.code == "PRODUCTION_ARCHIVE_HASH_MISMATCH"


def test_verify_detects_a_deleted_file(tmp_path, run_dir, archive_root):
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE
    _unseal(archive_dir)
    (archive_dir / "run/sources.json").unlink()

    with pytest.raises(ProductionArchiveError) as error:
        verify_production_archive(TARGET_DATE, archive_root=archive_root)
    assert error.value.code == "PRODUCTION_ARCHIVE_MISSING_FILE"


def test_verify_detects_an_added_file(tmp_path, run_dir, archive_root):
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE
    _unseal(archive_dir)
    (archive_dir / "run/smuggled.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ProductionArchiveError) as error:
        verify_production_archive(TARGET_DATE, archive_root=archive_root)
    assert error.value.code == "PRODUCTION_ARCHIVE_EXTRA_FILE"


def test_verify_detects_an_added_top_level_entry(tmp_path, run_dir, archive_root):
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE
    _unseal(archive_dir)
    (archive_dir / "notes.txt").write_text("hello", encoding="utf-8")

    with pytest.raises(ProductionArchiveError) as error:
        verify_production_archive(TARGET_DATE, archive_root=archive_root)
    assert error.value.code == "PRODUCTION_ARCHIVE_EXTRA_FILE"


def test_verify_detects_a_rewritten_manifest(tmp_path, run_dir, archive_root):
    """Rewriting the manifest to match tampered bytes still fails: the digest
    file is the second witness."""
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE
    _unseal(archive_dir)
    target = archive_dir / "run/sources.json"
    target.write_text("tampered", encoding="utf-8")
    manifest = json.loads((archive_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        if entry["path"] == "run/sources.json":
            entry["sha256"] = production_archive.sha256_bytes(target.read_bytes())
            entry["size_bytes"] = len(target.read_bytes())
    manifest["total_size_bytes"] = sum(e["size_bytes"] for e in manifest["files"])
    (archive_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ProductionArchiveError) as error:
        verify_production_archive(TARGET_DATE, archive_root=archive_root)
    assert error.value.code == "PRODUCTION_ARCHIVE_HASH_MISMATCH"


def test_verify_detects_a_missing_manifest(tmp_path, run_dir, archive_root):
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE
    _unseal(archive_dir)
    (archive_dir / MANIFEST_NAME).unlink()

    with pytest.raises(ProductionArchiveError) as error:
        verify_production_archive(TARGET_DATE, archive_root=archive_root)
    assert error.value.code == "PRODUCTION_ARCHIVE_MANIFEST_INVALID"


def test_verify_detects_a_missing_digest_file(tmp_path, run_dir, archive_root):
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE
    _unseal(archive_dir)
    (archive_dir / MANIFEST_SHA_NAME).unlink()

    with pytest.raises(ProductionArchiveError) as error:
        verify_production_archive(TARGET_DATE, archive_root=archive_root)
    assert error.value.code == "PRODUCTION_ARCHIVE_MANIFEST_INVALID"


@pytest.mark.parametrize(
    "digest",
    [
        pytest.param(b"deadbeef\n", id="too-short"),
        pytest.param(b"A" * 64 + b"\n", id="uppercase"),
        pytest.param(b"z" * 64 + b"\n", id="non-hex"),
        pytest.param(b"0" * 64, id="no-trailing-newline"),
    ],
)
def test_verify_rejects_a_malformed_digest_file(
    tmp_path, run_dir, archive_root, digest
):
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE
    _unseal(archive_dir)
    (archive_dir / MANIFEST_SHA_NAME).write_bytes(digest)

    with pytest.raises(ProductionArchiveError) as error:
        verify_production_archive(TARGET_DATE, archive_root=archive_root)
    assert error.value.code == "PRODUCTION_ARCHIVE_MANIFEST_INVALID"


def test_verify_rejects_a_manifest_for_another_date(tmp_path, run_dir, archive_root):
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE

    with pytest.raises(ProductionArchiveError) as error:
        verify_archive_integrity(archive_dir, target_date="2026-08-11")
    assert error.value.code == "PRODUCTION_ARCHIVE_MANIFEST_INVALID"


def test_verify_rejects_a_swapped_runtime_security_attestation(
    tmp_path, run_dir, archive_root
):
    """The attestation is covered by files[] *and* by the manifest's own
    runtime_security block, so replacing it cannot be made consistent by
    editing one of them alone."""
    _write_runtime_security(run_dir, _runtime_security_document())
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE
    _unseal(archive_dir)
    (archive_dir / "run/working/runtime_security.json").unlink()

    with pytest.raises(ProductionArchiveError) as error:
        verify_production_archive(TARGET_DATE, archive_root=archive_root)
    assert error.value.code == "PRODUCTION_ARCHIVE_MISSING_FILE"


def test_an_incomplete_archive_is_still_a_valid_archive(tmp_path, archive_root):
    """Archive validity is byte integrity, never business status."""
    run = tmp_path / "daytrade" / "runs" / TARGET_DATE
    run.mkdir(parents=True)
    (run / "sources.json").write_text("{}", encoding="utf-8")
    _archive(tmp_path, run)

    result = verify_production_archive(TARGET_DATE, archive_root=archive_root)

    assert result["result"] == RESULT_ARCHIVE_VERIFIED
    assert result["archive_status"] == ARCHIVE_STATUS_INCOMPLETE


# ------------------------------------------- historical Source Matrix ---


def test_the_source_matrix_lands_where_calibration_reads_it(
    tmp_path, run_dir, archive_root
):
    _archive(tmp_path, run_dir)
    expected_sha = production_archive.sha256_bytes(HISTORICAL_SOURCE_MATRIX.read_bytes())

    entry = registry_dir(archive_root) / f"{expected_sha}.yaml"
    assert entry.is_file()
    assert entry.read_bytes() == HISTORICAL_SOURCE_MATRIX.read_bytes()
    assert stat.S_IMODE(os.stat(entry).st_mode) == 0o444

    other = tmp_path / "different.yaml"
    other.write_text("sources: []\n", encoding="utf-8")
    resolution = resolve_historical_source_matrix_path(
        expected_source_matrix_sha256=expected_sha,
        target_source_matrix_path=other,
        source_matrix_registry_dir=registry_dir(archive_root),
    )
    assert resolution.status == "RESOLVED"
    assert resolution.path == entry


def test_an_existing_registry_entry_is_reused_not_rewritten(tmp_path, archive_root):
    payload = b"sources: []\n"
    sha = production_archive.sha256_bytes(payload)

    first = store_source_matrix_in_registry(
        archive_root=archive_root, source_matrix_bytes=payload, sha256=sha
    )
    before = os.stat(first).st_mtime_ns
    second = store_source_matrix_in_registry(
        archive_root=archive_root, source_matrix_bytes=payload, sha256=sha
    )

    assert second == first
    assert os.stat(first).st_mtime_ns == before


def test_a_registry_entry_whose_bytes_do_not_match_its_name_is_a_hard_error(
    tmp_path, archive_root
):
    payload = b"sources: []\n"
    sha = production_archive.sha256_bytes(payload)
    directory = registry_dir(archive_root)
    directory.mkdir(parents=True)
    (directory / f"{sha}.yaml").write_bytes(b"tampered\n")

    with pytest.raises(ProductionArchiveError) as error:
        store_source_matrix_in_registry(
            archive_root=archive_root, source_matrix_bytes=payload, sha256=sha
        )
    assert error.value.code == "PRODUCTION_ARCHIVE_REGISTRY_HASH_MISMATCH"
    assert (directory / f"{sha}.yaml").read_bytes() == b"tampered\n"


def test_verify_reports_an_absent_registry_entry_without_repairing_it(
    tmp_path, run_dir, archive_root
):
    _archive(tmp_path, run_dir)
    for entry in registry_dir(archive_root).iterdir():
        os.chmod(entry, 0o644)
        entry.unlink()

    result = verify_production_archive(TARGET_DATE, archive_root=archive_root)

    assert result["source_matrix_registry"] == "ABSENT"
    assert list(registry_dir(archive_root).iterdir()) == []


# ---------------------------------------------------------------- CLI ---


def test_archive_cli_prints_json_and_exits_zero(
    tmp_path, run_dir, archive_root, monkeypatch, capsys
):
    monkeypatch.setattr(
        production_archive,
        "archive_production_run",
        lambda target_date: _archive(tmp_path, run_dir),
    )

    assert archive_main(["--target-date", TARGET_DATE]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == RESULT_ARCHIVED


def test_verify_cli_prints_json_and_exits_zero(
    tmp_path, run_dir, archive_root, monkeypatch, capsys
):
    _archive(tmp_path, run_dir)
    monkeypatch.setattr(
        production_archive,
        "verify_production_archive",
        lambda target_date: verify_production_archive(
            target_date, archive_root=archive_root
        ),
    )

    assert verify_main(["--target-date", TARGET_DATE]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == RESULT_ARCHIVE_VERIFIED


def test_a_contract_violation_exits_two_with_the_code_on_stderr(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setattr(
        production_archive,
        "verify_production_archive",
        lambda target_date: verify_production_archive(
            target_date, archive_root=tmp_path / "missing-archive"
        ),
    )

    assert verify_main(["--target-date", TARGET_DATE]) == 2
    captured = capsys.readouterr()
    assert "PRODUCTION_ARCHIVE_SOURCE_MISSING" in captured.err
    assert captured.out == ""


def test_the_cli_exposes_only_target_date(capsys):
    for main in (archive_main, verify_main):
        with pytest.raises(SystemExit):
            main(["--archive-root", "/tmp/elsewhere", "--target-date", TARGET_DATE])
        with pytest.raises(SystemExit):
            main(["--force", "--target-date", TARGET_DATE])
        capsys.readouterr()


# ------------------------------------------------------ boundary rules ---


def test_the_archive_root_is_outside_the_git_work_tree():
    assert production_archive.ARCHIVE_ROOT.name == "daytrade-production-archive"
    assert production_archive.REPOSITORY_ROOT not in (
        production_archive.ARCHIVE_ROOT.parents
    )
    assert production_archive.ARCHIVE_ROOT.parent == (
        production_archive.REPOSITORY_ROOT.parent
    )


def test_the_module_never_reaches_the_network_or_git():
    """Asserted over the import graph, not over prose: the docstring may say
    the words, the code may not import the capability."""
    import ast

    source = (PROJECT_ROOT / "src" / "production_archive.py").read_text(
        encoding="utf-8"
    )
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            if node.module.startswith("src."):
                imported.add(node.module)

    forbidden = {
        "subprocess",
        "requests",
        "urllib",
        "urllib3",
        "httpx",
        "socket",
        "http",
        "ftplib",
        "src.source_fetch",
        "src.network_policy",
        "src.source_acquisition",
    }
    assert not (imported & forbidden), sorted(imported & forbidden)


def test_the_module_has_no_force_and_no_retention_path():
    source = (PROJECT_ROOT / "src" / "production_archive.py").read_text(
        encoding="utf-8"
    )
    # --target-date is the only CLI input: no --force, no --archive-root, no
    # --retention-days can be reached from a command line.
    assert source.count("add_argument(") == 1
    assert 'add_argument("--target-date"' in source
    # the only tree removal is our own staging directory
    assert source.count("shutil.rmtree") == 1
    assert "_force_rmtree(staging)" in source


def test_both_entry_points_exist_and_are_executable():
    for name in ("archive-production-run", "verify-production-archive"):
        script = PROJECT_ROOT / "scripts" / name
        assert script.is_file(), name
        assert os.access(script, os.X_OK), name
        assert "HUMAN-ONLY" in script.read_text(encoding="utf-8")


def test_the_archive_is_not_a_canonical_pipeline_command():
    """Sealing and verifying an archive are human operations, not stages.

    Neither is a ``src.cli`` subcommand, so neither can appear in the Canonical
    CLI Pipeline Order or be run as part of a nightly.
    """
    from src import cli

    choices = set(cli.build_parser()._subparsers._group_actions[0].choices)  # noqa: SLF001
    for name in ("archive-production-run", "verify-production-archive"):
        assert name not in choices, name


# ------------------------------- working/ Non-Business Sidecar contract ---


def test_the_sidecar_is_not_a_business_artifact():
    from src.contracts import RUN_ARTIFACT_ALLOWLIST, WORKING_SIDECAR_DIR

    assert WORKING_SIDECAR_DIR == "working"
    assert "working" not in RUN_ARTIFACT_ALLOWLIST


def test_an_absent_working_sidecar_is_normal(run_dir):
    assert validate_run_artifact_allowlist(run_dir) == ()


@pytest.mark.parametrize(
    "relative",
    [
        pytest.param("working/runtime_security.json", id="attestation"),
        pytest.param("working/event_source_extraction.json", id="event-extraction"),
        pytest.param("working/future_runtime_evidence.json", id="future-evidence"),
        pytest.param("working/subdir/evidence.json", id="nested-evidence"),
    ],
)
def test_the_business_allowlist_never_looks_inside_the_sidecar(run_dir, relative):
    """A new piece of runtime security evidence must not, by itself, turn a
    good business run into INVALID_RUN. What is inside ``working/`` is the
    Production Run Archive's business, not the Business Verifier's."""
    path = run_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")

    assert validate_run_artifact_allowlist(run_dir) == ()


def test_a_regular_file_named_working_is_rejected(run_dir):
    (run_dir / "working").write_text("not a sidecar", encoding="utf-8")

    assert validate_run_artifact_allowlist(run_dir) == ("working",)


def test_a_symlinked_working_is_rejected_and_never_followed(run_dir, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "runtime_security.json").write_text("{}", encoding="utf-8")
    (run_dir / "working").symlink_to(elsewhere, target_is_directory=True)

    assert validate_run_artifact_allowlist(run_dir) == ("working",)


def test_another_directory_name_is_not_a_sidecar(run_dir):
    (run_dir / "working2").mkdir()
    (run_dir / "workingx").mkdir()

    assert validate_run_artifact_allowlist(run_dir) == ("working2", "workingx")


def test_the_production_verifier_accepts_a_run_carrying_its_attestation(run_dir):
    from src.production_verify import verify_production_run

    _write_runtime_security(run_dir, _runtime_security_document())
    report = verify_production_run(
        run_dir, source_matrix_path=HISTORICAL_SOURCE_MATRIX
    )

    assert report.status == VERIFIED_CASE_C_NO_TRADE, report.errors


def test_the_production_verifier_accepts_unknown_sidecar_evidence(run_dir):
    """The forward-compatibility case, stated as a business outcome."""
    from src.production_verify import verify_production_run

    _write_runtime_security(run_dir, _runtime_security_document())
    (run_dir / "working" / "future_runtime_evidence.json").write_text(
        "{}", encoding="utf-8"
    )
    (run_dir / "working" / "subdir").mkdir()
    (run_dir / "working" / "subdir" / "evidence.json").write_text(
        "{}", encoding="utf-8"
    )

    report = verify_production_run(
        run_dir, source_matrix_path=HISTORICAL_SOURCE_MATRIX
    )

    assert report.status == VERIFIED_CASE_C_NO_TRADE, report.errors


def test_the_verifier_does_not_enumerate_sidecar_filenames():
    """Guard against the contract being re-tightened by accident."""
    source = (PROJECT_ROOT / "src" / "production_verify.py").read_text(
        encoding="utf-8"
    )
    assert "runtime_security.json" not in source
    assert "event_source_extraction.json" not in source


def test_the_whole_sidecar_is_archived_raw_including_unknown_evidence(
    tmp_path, run_dir, archive_root
):
    """Business allowlist and Archive source safety are different contracts:
    the verifier does not look inside working/, the Archive stores all of it
    byte-for-byte."""
    _write_runtime_security(run_dir, _runtime_security_document())
    (run_dir / "working" / "future_runtime_evidence.json").write_bytes(b"\x00\xffraw")
    (run_dir / "working" / "subdir").mkdir()
    (run_dir / "working" / "subdir" / "evidence.json").write_bytes(b"nested")

    _archive(tmp_path, run_dir)

    archived = archive_root / "runs" / TARGET_DATE / "run" / "working"
    assert (archived / "future_runtime_evidence.json").read_bytes() == b"\x00\xffraw"
    assert (archived / "subdir" / "evidence.json").read_bytes() == b"nested"
    manifest = json.loads(
        (archive_root / "runs" / TARGET_DATE / MANIFEST_NAME).read_text(
            encoding="utf-8"
        )
    )
    listed = {entry["path"] for entry in manifest["files"]}
    assert "run/working/future_runtime_evidence.json" in listed
    assert "run/working/subdir/evidence.json" in listed


def test_archive_source_safety_still_fail_closes_on_a_sidecar_symlink(
    tmp_path, run_dir, archive_root
):
    """The Archive's source safety scan is NOT relaxed by the business
    allowlist's tolerance: a symlink anywhere under the run aborts it."""
    _write_runtime_security(run_dir, _runtime_security_document())
    (run_dir / "working" / "link.json").symlink_to(
        run_dir / "working" / "runtime_security.json"
    )

    with pytest.raises(ProductionArchiveError) as error:
        _archive(tmp_path, run_dir)
    assert error.value.code == "PRODUCTION_ARCHIVE_SOURCE_UNSAFE_ENTRY"
    assert not (archive_root / "runs" / TARGET_DATE).exists()


def test_archive_source_safety_still_fail_closes_on_a_sidecar_fifo(
    tmp_path, run_dir, archive_root
):
    (run_dir / "working").mkdir(parents=True, exist_ok=True)
    os.mkfifo(run_dir / "working" / "pipe")

    with pytest.raises(ProductionArchiveError) as error:
        _archive(tmp_path, run_dir)
    assert error.value.code == "PRODUCTION_ARCHIVE_SOURCE_UNSAFE_ENTRY"
    assert not (archive_root / "runs" / TARGET_DATE).exists()


# ------------------------------------------------------ documentation ---


def test_the_contract_document_exists_and_states_the_hard_rules():
    text = (PROJECT_ROOT / "docs" / "production-run-archive.md").read_text(
        encoding="utf-8"
    )
    for required in (
        "archive-production-run",
        "verify-production-archive",
        "archive_manifest.json",
        "archive_manifest.sha256",
        "COMPLETE_VERIFIED",
        "INCOMPLETE",
        "ALREADY_ARCHIVED",
        "daytrade-production-archive",
        "working",
    ):
        assert required in text, required


def test_the_archive_is_documented_as_not_a_backup():
    text = (PROJECT_ROOT / "docs" / "production-run-archive.md").read_text(
        encoding="utf-8"
    )
    assert "backup" in text.lower()
    assert "同一マシン" in text


def test_the_nightly_operation_doc_points_at_the_archive_step():
    text = (PROJECT_ROOT / "docs" / "nightly-operation.md").read_text(encoding="utf-8")
    assert "production-run-archive.md" in text
    assert "archive-production-run" in text


def test_the_runs_readme_documents_the_sidecar_and_the_archive():
    text = (PROJECT_ROOT / "runs" / "README.md").read_text(encoding="utf-8")
    assert "working/" in text
    assert "production-run-archive.md" in text


def test_dated_runs_are_git_ignored_but_the_readme_is_not():
    text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "/runs/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/" in text
    assert "README.md" not in text.split("/runs/")[0]


# --------------------------------- historical Archive v1 read contract ---
#
# DTWO-2026-026 stopped writing v1 manifests; it did not stop reading them. A
# v1 archive was sealed while the Runtime Security Attestation was part of what
# "complete" meant, and its bytes are never rewritten, re-sealed or migrated --
# so verification has to keep judging it under exactly that older contract.


def _rewrite_as_v1(archive_dir: Path, *, runtime_security: dict | None) -> None:
    """Turn a freshly sealed v2 archive into the v1 archive it would have been.

    Only the manifest and its digest are rewritten; every archived byte under
    ``run/``, ``inputs/`` and ``verification/`` is left exactly as sealed, so
    what the test verifies afterwards is real archived content.
    """
    _unseal(archive_dir)
    manifest = json.loads((archive_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["schema_version"] = 1
    manifest["archive_version"] = ARCHIVE_VERSION
    manifest["source"]["runtime_security_git_head_sha"] = (
        runtime_security["git_head_sha"] if runtime_security else None
    )
    manifest["runtime_security"] = (
        {
            "status": runtime_security["status"],
            "path": runtime_security["path"],
            "sha256": runtime_security["sha256"],
        }
        if runtime_security
        else {"status": RUNTIME_SECURITY_MISSING, "path": None, "sha256": None}
    )
    raw = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    (archive_dir / MANIFEST_NAME).write_bytes(raw)
    (archive_dir / MANIFEST_SHA_NAME).write_bytes(
        (production_archive.sha256_bytes(raw) + "\n").encode("ascii")
    )


def _archived_attestation(archive_dir: Path) -> dict:
    payload = (archive_dir / "run/working/runtime_security.json").read_bytes()
    return {
        "status": RUNTIME_SECURITY_VALID,
        "path": "run/working/runtime_security.json",
        "sha256": production_archive.sha256_bytes(payload),
        "git_head_sha": "a" * 40,
    }


def test_a_historical_v1_archive_still_verifies(tmp_path, run_dir, archive_root):
    """TC-11 / AC-15: manifest schema, manifest SHA, file hashes and the legacy
    runtime-security evidence all still validate for a v1 archive."""
    _write_runtime_security(run_dir, _runtime_security_document())
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE
    _rewrite_as_v1(archive_dir, runtime_security=_archived_attestation(archive_dir))

    result = verify_production_archive(TARGET_DATE, archive_root=archive_root)

    assert result["result"] == RESULT_ARCHIVE_VERIFIED
    assert result["schema_version"] == 1
    assert result["runtime_security_status"] == RUNTIME_SECURITY_VALID
    assert result["stored_business_verification_status"] == VERIFIED_CASE_C_NO_TRADE


def test_a_v1_archive_is_still_validated_against_the_v1_schema(
    tmp_path, run_dir, archive_root
):
    """The v1 read path is the v1 contract, not a relaxed one: a v1 manifest
    missing its runtime_security block is invalid, exactly as it always was."""
    _write_runtime_security(run_dir, _runtime_security_document())
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE
    _rewrite_as_v1(archive_dir, runtime_security=_archived_attestation(archive_dir))

    _unseal(archive_dir)
    manifest = json.loads((archive_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    del manifest["runtime_security"]
    raw = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    (archive_dir / MANIFEST_NAME).write_bytes(raw)
    (archive_dir / MANIFEST_SHA_NAME).write_bytes(
        (production_archive.sha256_bytes(raw) + "\n").encode("ascii")
    )

    with pytest.raises(ProductionArchiveError) as error:
        verify_production_archive(TARGET_DATE, archive_root=archive_root)
    assert error.value.code == "PRODUCTION_ARCHIVE_MANIFEST_INVALID"


def test_a_v1_attestation_that_no_longer_matches_its_bytes_is_invalid(
    tmp_path, run_dir, archive_root
):
    """The historical evidence check itself is untouched: a v1 manifest whose
    recorded attestation disagrees with the archived file still fails."""
    _write_runtime_security(run_dir, _runtime_security_document())
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE
    attestation = _archived_attestation(archive_dir)
    attestation["sha256"] = "f" * 64
    _rewrite_as_v1(archive_dir, runtime_security=attestation)

    with pytest.raises(ProductionArchiveError) as error:
        verify_production_archive(TARGET_DATE, archive_root=archive_root)
    assert error.value.code == "PRODUCTION_ARCHIVE_MANIFEST_INVALID"


def test_an_unknown_manifest_generation_is_refused(tmp_path, run_dir, archive_root):
    """Neither v1 nor v2 rules are applied to bytes that claim to be neither."""
    _archive(tmp_path, run_dir)
    archive_dir = archive_root / "runs" / TARGET_DATE
    _unseal(archive_dir)
    manifest = json.loads((archive_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["schema_version"] = 3
    raw = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    (archive_dir / MANIFEST_NAME).write_bytes(raw)
    (archive_dir / MANIFEST_SHA_NAME).write_bytes(
        (production_archive.sha256_bytes(raw) + "\n").encode("ascii")
    )

    with pytest.raises(ProductionArchiveError) as error:
        verify_production_archive(TARGET_DATE, archive_root=archive_root)
    assert error.value.code == "PRODUCTION_ARCHIVE_MANIFEST_INVALID"
