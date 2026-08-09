# Nightly Research

対象日: 2026-08-10
前営業日: 2026-08-07
research_cutoff: 2026-08-07T20:00:00+09:00
research_executed_at: 2026-08-09T18:32:58+09:00

## Discovery結果

- YAHOO_JP_VOLUME_RANKING: FOUND。ALL_MARKETS、TOP50、更新時刻は2026/08/07 18:40。Evidence: `source_pages/yahoo_volume.html`。
- YAHOO_JP_GAIN_RANKING: FOUND。ALL_MARKETS、TOP50、更新時刻は2026/08/07 18:40。Evidence: `source_pages/yahoo_gain.html`。
- JPX_TDNET: PARSE_FAILED。保存した2026-08-07の1ページ目では追加ページが示されていた。research_window全体の抽出が完了していないため、開示0件を確認済みとは扱わない。
- Discovery UnionはYahooの2経路のみから99銘柄。TDnet経路は未完了のため、TDnet由来候補は採用していない。

## Source Audit

- JPXの市場休業日情報では2026-08-11が休業日であり、2026-08-10と2026-08-07は休業日に含まれていない。JPXの土日・祝日休業ルールと合わせ、このrunでは2026-08-10を対象日、2026-08-07を前営業日とする。
- DiscoveryはSource Matrixの経路であるVOLUME_RANKING、PRICE_GAIN_RANKING、TIMELY_DISCLOSUREだけを使用した。
- Yahooランキングの順位と表示値はDiscovery理由として保存しただけで、最終Rankingの評価値には使用していない。

## データ欠落・矛盾

- TDnetのresearch_window全体に対するページ抽出が未完了。
- Discovery Unionの候補について、銘柄基本情報、Yahoo/株探OHLCV、JPX呼値、決算予定、候補別適時開示、関連ニュースを検証できていない。
- 未確認値や欠落値は`market_data.json`へ補完していない。
- 出典間の矛盾は推測で解決していない。

## cutoff後の部分調査

- 初期成果物の生成後、read-onlyのmarket researcherが追加の部分結果を返した。部分結果であり、`DATA_UNAVAILABLE`判断は変更しない。
- research_window内のTDnet部分一致候補として2181、4564、4767、6400、6439、6993が報告された。
- 複数の低価格候補でOHLCVの部分照合が行われたが、銘柄基本情報、売買単位、証券種別、TOPIX500該当性、候補別呼値は未完了のまま。
- 1360と1306ではYahoo/株探の出来高に1株差の矛盾が報告された。
- 6439はYahooの`.T`テンプレートが名証上場に一致せず、株探のみOHLCV行があるためSINGLE_SOURCE_ONLYと報告された。
- JPX_EARNINGS_SCHEDULEは、6740と8729について2026-08-10の予定ありとしてFOUND報告があった。
- Source Matrixに沿った候補調査が完了しておらず、取引重要データも欠落しているため、これらの部分結果は取引可能な`market_data.json`レコードへ昇格していない。

## Rankingに使わなかったDiscovery情報

- Yahooランキングの順位、取引値、値動き、出来高、上昇率はDiscovery evidenceとしてのみ使用した。
- TDnetは未完了のため、候補発見にも最終比較にも使用していない。

## Codexによる比較評価

TDnet Discoveryと候補別の取引重要データが未完了である。ELIGIBLE候補の比較まで到達していないため、推奨判断は`DATA_UNAVAILABLE`とする。
