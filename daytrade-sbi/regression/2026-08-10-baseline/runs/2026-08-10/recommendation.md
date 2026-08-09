# 翌営業日注文案

対象日: 2026-08-10
戦略バージョン: v1
設定SHA-256: 5c7f803bf4da07155165b3b1f9075e577746280aa886f2b3d1348643c0b38d82
情報カットオフ: 2026-08-07T20:00:00+09:00

判定:
DATA_UNAVAILABLE

理由:
- JPX_TDNETのDiscoveryがresearch_window全体で抽出完了しておらず、適時開示由来の候補と除外判断が不完全。
- Discovery Unionの候補について、銘柄基本情報、Yahoo/株探OHLCV、JPX呼値などの取引重要データを検証できていない。
- screen-marketでELIGIBLE候補が作成されておらず、DATA_UNAVAILABLEとして注文値は作成しない。

候補パイプライン概要:
- Discovery候補: 99件
- Research完了: 0件
- Research未完了: 0件
- DATA_UNAVAILABLE: 99件
- ELIGIBLE: 0件
- REJECTED: 0件

主なSource状態:
- JPX_TDNET: PARSE_FAILED https://www.release.tdnet.info/inbs/I_list_001_20260807.html (research_window全体のTDnetページ巡回が完了していない。)

注意事項:
- 必要な市場データが揃わず、取引判断まで到達していない。
