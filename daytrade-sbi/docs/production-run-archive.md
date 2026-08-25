# Production Run Archive Contract v1

Production Nightly Runの証跡は`runs/<target-date>/`に残ります。しかしこのディレクトリは
**Operational**です。

- 次回のRuntime Security Preflightは`git_clean`を要求するため、`runs/YYYY-MM-DD/`は
  git ignoreされている（[`.gitignore`](../.gitignore)）
- Pipelineが途中で停止すると半端な状態で残る
- Humanが整理・削除でき、そうすると「その夜に実際に何が起きたか」の唯一の記録が消える

Production Run Archive Contract v1は、この`runs/<target-date>/`を**byte-exactにcopyし、
SHA256 manifestで封をした複製**をリポジトリ外へ作ります。実装は
[`src/production_archive.py`](../src/production_archive.py)、契約testは
[`tests/test_production_archive.py`](../tests/test_production_archive.py)です。

## これは何ではないか

- **Business Verifierではない**。Business Validationは既存の
  [`src/production_verify.py`](../src/production_verify.py)の
  `verify_production_run`をそのまま呼び、その報告を逐語で保存するだけ。
  Archive側にBusiness Logicの写しは作らない
- **backupではない**。Archiveは**同一マシン**の上にある。disk故障・盗難・
  filesystem破壊は防げない。off-site backupが必要ならそれは別の運用であり、
  この契約はそれを代替しない
- **Production Claudeから到達できるものではない**。2つのentry pointはどちらも
  Human専用scriptで、canonical `src.cli` subcommandではないため、
  Production Managed Policyのexec-form allowlist（`APPROVED_SUBCOMMANDS`）に
  載ることが構造的にできない
- **retention機構ではない**。古いArchiveを消す機能は無い。削除はHumanが行う

## Archive Root

```text
<repository root>/../daytrade-production-archive/
  runs/<target-date>/
    run/                     # runs/<target-date>/ のbyte-exact copy
    inputs/
      source_matrix.yaml
      issuer_domain_registry.yaml
    verification/
      production_verify.json
    archive_manifest.json
    archive_manifest.sha256
  registries/source_matrix/<sha256>.yaml
  .staging/
```

Archive Rootはgit work treeの外にある**repositoryの兄弟directory**で、
`src/production_archive.py`が固定します。CLIから移動できません。
特定OSの絶対パスはドキュメントにもコードにもhardcodeしません。

## 2つのentry point（Human専用）

```bash
scripts/archive-production-run --target-date <YYYY-MM-DD>
scripts/verify-production-archive --target-date <YYYY-MM-DD>
```

`--target-date`が唯一の入力です。`--force`も`--archive-root`も`--retention-days`も
存在せず、追加しません。exit codeは`0`（成功）と`2`（契約違反）だけです。
成功時はmachine-readableなJSON 1行をstdoutへ書きます。

## 不変条件

1. **Operational Runはread-only source**。Archiveは`runs/<target-date>/`へ
   書き込み・rename・正規化・再serialize・削除を一切行わない
2. **raw byte copy**。copy後にdestinationから再度SHA256を取り、一致しなければ
   Hard Error。JSONの整形やencoding変換は行わない
3. **symlinkを辿らない**。run tree内のsymlink・FIFO・socket・device nodeは
   `PRODUCTION_ARCHIVE_SOURCE_UNSAFE_ENTRY`でscan全体を停止する。
   部分的なArchiveは作らない
4. **atomic finalize**。Archiveは`.staging/`内で組み立て、完成・検証・sealの後に
   `os.replace`ただ1回で所定の位置へ移す。作りかけのArchiveが
   `<archive root>/runs/`配下に見えることはない
5. **上書きしない**。既存Archiveがあるとき、sourceが完全一致すれば
   `ALREADY_ARCHIVED`（何も書かない）。1 byteでも乖離していれば
   `PRODUCTION_ARCHIVE_SOURCE_DIVERGED`。既存Archiveが壊れていれば
   `PRODUCTION_ARCHIVE_EXISTS_INVALID`で停止し、修復も再生成もしない
6. **stale stagingは削除しない**。`.staging/`に対象日の残骸があれば
   `PRODUCTION_ARCHIVE_STAGING_EXISTS`で停止する。自分が作ったstagingだけは
   失敗時に片付ける
7. **INCOMPLETEでもArchiveする**。途中で止まった夜の証跡を失うことは、
   その証跡を保存することより厳密に悪い
8. **network access無し・git実行無し**。この経路にHTTPもgitも存在しない

## Manifest

[`schemas/production_archive_manifest.schema.json`](../schemas/production_archive_manifest.schema.json)。

`archive_manifest.json`は`files[]`に`run/` `inputs/` `verification/`配下の全fileを
path順で列挙し、それぞれの`size_bytes`と`sha256`を持ちます。manifest自身と
`archive_manifest.sha256`は意図的に自己列挙しません。
`archive_manifest.sha256`はmanifestの生byteのSHA256（64桁小文字hex + LF、計65 byte）で、
manifestを書き換えて辻褄を合わせる改竄に対する第2の証人です。

### `archive_status`

| 値 | 意味 |
| --- | --- |
| `COMPLETE_VERIFIED` | Business Verificationが`VERIFIED_*`のいずれか **かつ** Runtime Security Attestationが`VALID` |
| `INCOMPLETE` | それ以外（Business INVALID_RUN、Attestation欠落・破損・日付不一致） |

`archive_status`はArchiveの妥当性ではなく、**Archiveされた夜の状態**を表します。
`INCOMPLETE`なArchiveも完全に妥当なArchiveであり、`verify-production-archive`は
byte整合性だけで判定します。

## `working/` Non-Business Sidecar

`runs/<date>/working/`は**Non-Business Sidecar**です。現在ここに入るのは
`runtime_security.json`（Production LauncherのRuntime Security Attestation）と
`event_source_extraction.json`（Event AI Classificationのlocal作業出力）ですが、
**この2つに固定された契約ではありません**。

これらはcanonical `src.cli` stageの成果物ではなく、`config_sha256` /
`strategy_version`を持たず、どのTrust Chainにも属しません。したがって
Business Artifactの`RUN_ARTIFACT_ALLOWLIST`へ入れることはできません。
一方でこれを「予期しないartifact」として扱うことも誤りで、そうすると
Managed Policy下で実行された証拠であるAttestationを持つがゆえに、
実際のProduction Nightly Runがすべて`INVALID_RUN`になってしまいます。

そこで[`src/contracts.py`](../src/contracts.py)の
`validate_run_artifact_allowlist`は`working/`をsidecar directoryとして認識し、
**丸ごとskipします**。

**Business Verifierは`working/`の内部を列挙しません。** 内部のfile名を
Business Artifact Allowlistとして扱うことはしません。将来`working/`へ新しい
Runtime Security Evidenceが追加されても、それだけを理由にBusiness Runを
`INVALID_RUN`にしてはならないからです。中身の検証はProduction Archive側の責務です。

`working/`について検査するのは**identityだけ**で、そこはfail-closedです。

| entry | 判定 |
| --- | --- |
| `working/`（real directory、非symlink） | Non-Business Sidecarとしてskip |
| `working/<何でも>`（nested directory含む） | Business判定では検査しない |
| `working`という名のregular file | 予期しないartifact → reject |
| `working`という名のsymlink（辿らない） | 予期しないartifact → reject |
| `working2` / `workingx`等の別directory | 予期しないartifact → reject |

**Business Artifact allowlistとArchive source safetyは別契約です。** Archiveは
`working/`配下も含めてrun tree全体をraw byteでsnapshotし、そのsource safety scanは
元の契約どおりsymlink・FIFO・socket・device nodeを
`PRODUCTION_ARCHIVE_SOURCE_UNSAFE_ENTRY`でfail-closeします。Business側が
`working/`の中身に寛容であることは、Archive側のscanを緩めません。

そのうえでArchiveは`run/working/runtime_security.json`を**Runtime Security証跡**として
Business Artifact chainとは独立に分類し、`VALID` / `MISSING` / `INVALID`を
manifestへ記録します。`git_head_sha`はschema-validなAttestationからのみ読み、
gitコマンドで推測することはありません。

## 歴史的Source Matrix Registry

Archiveは、その夜に有効だったSource Matrixの生byteを
`registries/source_matrix/<sha256>.yaml`へcontent-addressで保存します。
これは[`src/selection_calibration.py`](../src/selection_calibration.py)の
`resolve_historical_source_matrix_path`が読む layout そのものです。
既存entryは決して上書き・修復しません。byte一致なら再利用、不一致なら
`PRODUCTION_ARCHIVE_REGISTRY_HASH_MISMATCH`です。そのfileは過去runのTrust Chain
再検証が依存する証拠だからです。

## Sealed permission

完成したper-run Archiveはfile `0444`・directory `0555`にします。これは
**誤操作防止**であって暗号学的なimmutability保証ではありません。改竄の
*検出*はmanifestとその digest が担います。

## Error code

| code | 意味 |
| --- | --- |
| `PRODUCTION_ARCHIVE_TARGET_DATE_INVALID` | `--target-date`がYYYY-MM-DD（実在日付）でない |
| `PRODUCTION_ARCHIVE_SOURCE_MISSING` | run directory / 入力file / Archiveが無い |
| `PRODUCTION_ARCHIVE_SOURCE_NOT_DIRECTORY` | run pathがdirectoryでない |
| `PRODUCTION_ARCHIVE_SOURCE_UNSAFE_ENTRY` | symlink / FIFO / socket / device node |
| `PRODUCTION_ARCHIVE_STAGING_EXISTS` | 対象日のstale stagingが残っている |
| `PRODUCTION_ARCHIVE_EXISTS_INVALID` | 既存Archiveが検証を通らない |
| `PRODUCTION_ARCHIVE_SOURCE_DIVERGED` | Operational Runが既存Archiveと乖離した |
| `PRODUCTION_ARCHIVE_MANIFEST_INVALID` | manifest欠落 / schema違反 / 未sort / 集計不一致 |
| `PRODUCTION_ARCHIVE_HASH_MISMATCH` | 生byteのSHA256がmanifestと不一致 |
| `PRODUCTION_ARCHIVE_MISSING_FILE` | manifest記載のfileがArchiveに無い |
| `PRODUCTION_ARCHIVE_EXTRA_FILE` | manifestに無いfileがArchiveにある |
| `PRODUCTION_ARCHIVE_REGISTRY_HASH_MISMATCH` | registry fileのbyteが自身の名前と不一致 |
| `PRODUCTION_ARCHIVE_FINALIZE_FAILED` | 最終moveまたはregistry書き込みの失敗 |

## 運用手順

1. Production Nightly Runが終了する（`NO_TRADE` / `DATA_UNAVAILABLE` /
   `REJECTED` / Discovery未完了でも同じ）
2. Production Claude sessionを終了する
3. Humanが通常のshellから実行する。

   ```bash
   scripts/archive-production-run --target-date <YYYY-MM-DD>
   ```

4. 結果JSONの`archive_status`を確認する。`INCOMPLETE`はArchive失敗ではなく、
   その夜が途中で止まったことを意味する
5. 後日、証跡を参照する前に検証する。

   ```bash
   scripts/verify-production-archive --target-date <YYYY-MM-DD>
   ```

   `stored_business_verification_status`と
   `current_business_reverification_status`が食い違う場合、それはArchiveの改竄では
   なく**Production Verifierの業務規則が変わった**ことを意味します。報告はしますが、
   Archiveを「直す」ことはしません。
