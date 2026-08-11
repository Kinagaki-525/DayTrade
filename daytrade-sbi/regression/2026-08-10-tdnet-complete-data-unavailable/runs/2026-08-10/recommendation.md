# 翌営業日注文案

対象日: 2026-08-10
戦略バージョン: v1
設定SHA-256: 5c7f803bf4da07155165b3b1f9075e577746280aa886f2b3d1348643c0b38d82
情報カットオフ: 2026-08-07T20:00:00+09:00

判定:
DATA_UNAVAILABLE

理由:
- Discovery completed with a 972-ticker union from Yahoo ranking pages and TDnet disclosures, but full candidate research was not completed.
- Listed-company data, Yahoo/Kabutan OHLCV confirmation, JPX tick size, earnings schedule, and related checks are unavailable for the full Discovery Union.
- screen-market produced no ELIGIBLE candidates, so no order prices are created.

候補パイプライン概要:
- Discovery候補: 972件
- Research完了: 0件
- Research未完了: 0件
- DATA_UNAVAILABLE: 972件
- ELIGIBLE: 0件
- REJECTED: 0件

主なSource状態:
- JPX_TDNET: FOUND https://www.release.tdnet.info/inbs/I_list_001_20260807.html (Parsed 1,627 TDnet disclosure rows from 2026-08-07 00:00 through 20:00.)
- JPX_TDNET: FOUND https://www.release.tdnet.info/inbs/I_list_001_20260806.html (Parsed all nine 2026-08-06 TDnet pages and found zero rows inside 20:00-23:59.)

注意事項:
- 必要な市場データが揃わず、取引判断まで到達していない。
