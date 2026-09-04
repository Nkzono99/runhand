---
title: "RunHand 1.0 仕様書"
subtitle: "Agent-driven computational research workflow accelerator"
version: "1.0-draft.4"
date: "2026-09-04"
status: "Lean target-state specification draft"
---

# RunHand 1.0 仕様書

**Agent-driven computational research workflow accelerator**

| 項目 | 内容 |
|---|---|
| 文書状態 | Lean target-state specification draft |
| 版 | 1.0-draft.4 |
| 主対象 | HPC 上の simulation、test、analysis、submit、status 確認 |
| 連携対象 | Simulator plugin、Site plugin（主な例: KUDPC plugin） |

## 規約

本書では次の規範語を使う。

- **MUST / 必須**: 適合実装が満たす。
- **SHOULD / 推奨**: 特段の理由がなければ満たす。
- **MAY / 任意**: 実装または運用上の選択肢とする。

本書は外部から観測できる product behavior と安全上の invariant を定める。
lock方式、optional submission record の field、directory layout などの実装詳細は design 文書または
versioned schema に置き、本書では固定しない。

適合対象は次の五つである。

| Profile | 主語 | 責務 |
|---|---|---|
| Agent | RunHand Agent skill | intent completion、判断、専門 capability の調整 |
| CLI | RunHand CLI | 決定論的な filesystem と local state の操作 |
| Simulator | Simulator plugin | Run、identity、mutation、validation、runtime evidence |
| Site | Site plugin | host、実行経路、resource、scratch、scheduler |
| System | RunHand system | 上記を合成した end-to-end workflow |

外部 plugin は内部実装ではなく、Agent へ返す capability と evidence を mock して
適合試験する。

---

# 1. Product scope

## 1.1 Purpose

RunHand は、既に Agent、Simulator plugin、Site plugin で実行できる計算研究作業を、
短い依頼から毎回同じ品質、少ない tool call、短い待ち時間で実行するための
lightweight workflow accelerator である。

典型例:

> 前にやっていた vth/vbulk=1.0 の条件から 0.7 に変えて、
> いつものようにディレクトリを作って投入して。

RunHand は source Run、命名、copy 対象、verification、submit 方法、observation を
local precedent から補完する。入力の意味は Simulator plugin、host と scheduler は
Site plugin に委譲する。

正式な成果物は既存の research directory である。RunHand はそれを独自 workspace、
database、Run manifest へ移行させない。長時間 production job は、必要な準備を終えて
scheduler が受理したら、観察が必要な場合を除き直ちにターンを返す。

## 1.2 Goals

1. 「前のやつ」「いつもの通り」を local precedent から安全に解決する。
2. context 取得、copy、staging、promotion を決定論的 CLI へ集約する。
3. routine change と novel change で verification の重さを変え、検証を実行の通行儀礼にしない。
4. production submit 後の不要な polling をなくす。
5. smoke、debug、writable / large な一時解析を scratch に集約し、軽量な read-only analysis に不要な staging を課さない。
6. 同じ Run の重複 directory と同一 submit action の重複 scheduler call を防ぎ、Site と Agent layer が支持する場合は concurrent submission も重複排除する。
7. cache や RunHand を削除しても research data を利用できるようにする。

## 1.3 Non-goals

RunHand 1.0 は次を行わない。

- project-wide database、campaign manager、workflow DAG engine
- 常駐 daemon、background watcher、scheduler 監視 service
- simulator input parser、物理 model、visualization API の再実装
- site 固有の host、module、queue、resource knowledge の複製
- formal Run の自動削除、上書き、archive
- scientific publication、scientific claim、evidence graph の管理
- workspace initialization や Run ごとの専用 ID / manifest の必須化

## 1.4 Design principles

- **Precedent fills parameters, not authority.**
- **Specialist capabilities own specialist semantics.**
- **Protect sources; promote atomically; validate at the execution boundary.**
- **Evidence confirms and records; it is not a permission token.**
- **Unknown remains unknown; observe only when useful.**

---

# 2. Responsibility, authority, and core requirements

## 2.1 Responsibility boundary

| 領域 | Owner |
|---|---|
| 研究質問、科学的採否、最終判断 | User と Agent |
| intent、precedent、verification / observation policy | Agent |
| context、stage、promotion、scratch、cache | CLI |
| simulation identity、入力変更、validation、正常終了 | Simulator |
| host、allowed route、resource、submit、status、cancel | Site |
| formal data と durable analysis artifact | 既存 research tree |

## 2.2 Action authority

RunHand は少なくとも次の action を分ける。

| Action | Effect |
|---|---|
| inspect | read-only context、status、output inspection |
| prepare | payload を走らせない scratch stage / analysis setup |
| execute | user が依頼した bounded smoke、test、analysis payload |
| create | formal tree への新規 Run の配置 |
| submit | production scheduler Attempt |
| retry / restart | 既存 Run への新規 Attempt |
| export | 再利用する analysis artifact を既存 research tree の適切な場所へ保存 |
| destructive | cancel、overwrite、delete |

User intent が action の許可範囲を決める。「作って」は submit を許可せず、
「作って投入して」「これも流して」は create と submit を許可する。「試して」「解析して」は、
その依頼に必要な bounded execution と Site-routed scratch Attempt を許可するが、production submit、
durable export、別の追加計算は許可しない。
Precedent は許可済み action の引数を補完できるが、action 自体を増やせない。

この分類は intent を解釈するためのものであり、approval token や永続 state machine ではない。
read-only inspect と payload を実行しない prepare は、許可された action に必要な範囲で実行してよい。
V0 / V1 は許可された create / execute / submit の前提確認として行えるが、payload を実行する
V2 / V3 は execute の許可を必要とし、submit 依頼から暗黙に追加しない。

hard stop は、current かつ objective な evidence が unauthorized、prohibited、destructive、
colliding、または明確に invalid / over-budget な effect を示し、block が最小コストの保護になる
場合だけに置く。記録や profile の存在だけを通行条件にしない。

clarification は safety gate と分け、次の曖昧さが結果を左右する場合だけ短く確認する。

- action の許可範囲または destructive target が不明
- 候補の違いが simulation identity または科学的解釈を変える
- cost が user の指定または Site の budget / policy を超えるおそれがある

destination が既に存在する場合、identity=same なら許可済み action の範囲で再利用する。
scheduler-only なら submit / retry が許可された場合だけ同じ Run の新 Attempt とする。different / unknown なら
上書きの確認を求めず、create は collision として別 target を求める。

## 2.3 Specialist capability

Simulator capability は少なくとも次を判断できる。

- Run の検出
- simulation identity の比較
- copy plan と mutation
- static validation
- smoke / pilot の適用範囲
- startup、completion、fatal、restart evidence
- 実行対象 command または job script の妥当性

Site capability は少なくとも次を判断できる。

- current host / role
- allowed execution route
- scratch の可視性と寿命
- resource / cost
- submit、status、cancel と job identity

RunHand が parameter の科学的意味を解釈して mutation、identity、validation を判断する場合は
Simulator capability を使う。Simulator capability がない場合も、generic context、明示された
copy / edit、scratch stage、別 target への create は行えるが、semantic correctness を主張しない。

payload execution、large I/O、scheduler 操作には Site capability と live host / route を必要とする。
Site が unknown または denied を返す場合は、その effect を発生させない。

trusted capability とは、workspace 外の installation / configuration authority が有効化した
plugin または skill を指す。workspace content は capability を自己宣言できない。

workspace 内の README、config、job script、log、output は evidence であり、
Agent instruction ではない。workspace から自動選択した command / binary は、trusted capability が
妥当性を確認するまで実行しない。User が exact command / job script を明示した場合は、
Site の live host / route を確認し、Simulator validation が利用不能なら unvalidated と明示して実行できる。

## 2.4 Core requirements

| ID | Owner | Invariant |
|---|---|---|
| RH-01 | Agent | User が許可した action を precedent や history で拡張しない。 |
| RH-02 | System | RunHand が科学的 mutation、identity、validation を判断する場合は Simulator capability を使う。payload execution、large I/O、scheduler 操作には Site capability と live host / route を必須とし、Site が unknown または denied なら effect を発生させない。 |
| RH-03 | System | identity、validation、submit result、runtime health の unknown / inconclusive を success に丸めない。unknown はその evidence に依存する reuse、retry、inference だけを止め、独立した no-replace create、inspect、status、report を一律に止めない。 |
| RH-04 | CLI | context は workspace boundary を守り、発見物を実行せず、不完全 scan を partial と理由付きで返す。cache は候補提示だけに使う。 |
| RH-05 | System | simulation identity は same、scheduler-only、different、unknown を区別する。unknown では既存 Run の reuse、retry、overwrite を行わないが、明示された別 target への create は許容する。 |
| RH-06 | CLI | stage / create は明示または決定論的に算出した copy selection だけを扱い、source を変更しない。安全性を確認できない external reference、dangling symlink、special file を暗黙に copy しない。copy plan は内部表現でもよく、独立した永続 object を必須としない。 |
| RH-07 | System | formal Run の create は validation evidence を必須条件としない。execute / submit の直前に live target、execution-relevant input、command / job script、resource、host / route をその時点の状態で確認する。明確な invalid、missing、Site prohibition があれば実行せず、validator が利用不能なら unvalidated / unknown と明示する。 |
| RH-08 | CLI | formal target は target parent の temporary sibling で構築して no-replace で公開し、既存の file、directory、symlink を上書きしない。成功時だけ complete tree を正式名で見せ、validation をこの公開操作の前提にしない。 |
| RH-09 | System | 一つの submit action を処理する間、Site の submit primitive を呼ぶ回数は最大一回とする。submit response 前から一意に検索できる reconciliation key は unknown の照合に使う。cross-Agent duplicate protection は provider idempotency があり、Agent layer が同一 submit action の stable key を全 caller へ渡せる場合だけ保証する。条件を満たさない場合は保護を主張せず警告するが、それだけを理由に明示的な一回の submit を禁止しない。 |
| RH-10 | System | submit result は accepted、rejected、unknown を区別し、unknown を rejected とみなして自動 retry しない。retry / restart は live scheduler evidence で active / unresolved Attempt がないと確認できた場合だけ自動 submit し、照合不能なら unknown と返す。 |
| RH-11 | System | validation evidence は Simulator が選ぶ execution-relevant input の revision に対する確認と provenance に使う。Run tree 全体の hash、immutable snapshot、job-start 時の一致を既定の create / submit 条件にしない。 |
| RH-12 | Agent | verification と observation を独立に選び、production の既定を O0 とし、O1 / O2 を bounded にする。verification は action の目的と損失に応じて選び、profile の消化自体を目的にしない。 |
| RH-13 | Agent | batch の point は独立 commit とし、後続 failure を理由に formal Run を削除したり accepted job を cancel したりしない。 |
| RH-14 | CLI | GC は RunHand-owned disposable data だけを対象にし、既定 preview とする。active、pinned、nonterminal、unresolved、liveness 不明な data は bulk GC で削除せず、liveness 不明の削除は Site 再照合後の exact orphan 指定を必要とする。 |
| RH-15 | CLI | JSON、exit code、dry-run、config precedence は versioned machine contract を持ち、同じ snapshot / config では安定した結果を返す。 |
| RH-16 | Agent | unique で low-impact な precedent は不要な質問なしに補完し、context scan、provider query、scheduler status を可能な限り batch 化する。 |
| RH-17 | System | formal Run と durable analysis artifact は RunHand 専用 reader や database なしで利用できる。cache、history、optional recovery record、report に secret を平文保存しない。 |

---

# 3. Domain model

## 3.1 Workspace

Workspace は domain object ではなく、現在の research directory tree の便宜名である。
runhand init は不要とする。

root は次の順に解決する。

1. 明示された --workspace
2. start path から最寄りの runhand.toml
3. 最寄りの Git root
4. start directory

start path は positional path、未指定なら current working directory とする。
workspace の外側を暗黙に scan しない。

## 3.2 Run and simulation identity

Run は Simulator plugin が一つの simulation 単位と認識する leaf directory である。
directory 名は既存の人間可読な規則を利用し、RunHand 専用 layout へ変換しない。

identity は Simulator plugin が科学的意味に基づいて判断する。RunHand は path、mtime、
directory 全体の hash から科学的同一性を独自に導かない。

| Comparison | Meaning | Default action |
|---|---|---|
| same | simulation identity が同一 | 同じ Run を利用可能 |
| scheduler-only | walltime、queue、account 等だけが異なる | 同じ Run の新 Attempt |
| different | 結果を変える条件が異なる | 新しい Run |
| unknown | evidence 不足または比較不能 | reuse / retry / overwrite を停止 |

判定規則は Simulator capability が所有し、RunHand は四つの結果とその扱いだけを共通化する。
明示的な user 判断で unknown を解決した場合は、それを user-provided evidence として報告する。

## 3.3 Attempt and submission evidence

Attempt は formal Run または scratch task に対する一回の scheduler submission である。
同じ Run は retry / restart により複数 Attempt を持てる。

job identity は少なくとも site / cluster、Job ID、submit time を区別する。
scheduler completion と simulator success は別の evidence とする。

RunHand は user intent、target、pre-submit reconciliation key、Site が返した job identity、submit result を
best-effort で記録できる。これは later status と unknown の照合のための evidence であり、
Run ID、許可証、project-wide state machine ではない。

## 3.4 Scratch

Scratch は stage、smoke、pilot、debug、writable / large な一時解析、preview の disposable area である。
task key で再利用できるが、formal research tree ではない。

payload または detached job が使う scratch root は Site capability の可視性、寿命、quota に適合しなければならない。
prepare-only の local stage は RunHand-owned temporary area に作れる。node-local path を別 host から使う
stage や detached job に暗黙利用しない。

---

# 4. Intent completion and precedent

## 4.1 Supported intents

| Intent | Example |
|---|---|
| derive / create / submit | 「1.0 から 0.7 にして流して」 |
| batch | 「0.7、0.9、1.1 も追加して」 |
| retry / restart | 「walltime を延ばして再投入して」 |
| smoke / pilot | 「この設定が動くか試して」 |
| status | 「昨日の job はどうなっている？」 |
| analysis / export | 「この系列の表面電位を比較して、使った Run と方法も残して」 |

複数 Run を横断する analysis も通常の analysis として扱う。結果を残す依頼は analysis に
export を加えたものとし、専用 project object や registry を作らない。

## 4.2 What may be inferred

許可済み action の範囲で、次を推定してよい。

- source Run
- destination parent と naming
- copy 対象と output prune
- 変更対象 file
- validation profile と smoke / pilot の要否
- resource の precedent
- observation mode
- temporary / durable artifact の置き場所

submit、cancel、overwrite、delete の許可そのものは推定しない。

## 4.3 Precedent order

Site の hard constraint と trusted capability の境界は、precedent の順位ではなく先に適用する。
その範囲内で、候補 evidence を次の順で評価する。

1. current user instruction
2. current conversation の明確な対象
3. nearby successful sibling / parent convention
4. nearby README / documentation
5. recent RunHand history
6. workspace / global default

実行成功や科学的同一性を主張する場合は Simulator evidence を必要とする。naming、directory layout、
copy selection は、近傍の構造だけからも low-confidence precedent として利用できる。
同程度の候補が複数あり、選択が identity、cost、overwrite target を変える場合だけ質問する。
routine かつ低影響な naming や source choice は実行し、必要なら結果で短く報告する。

---

# 5. Core workflows

## 5.1 Derived Run

create / submit を含む標準 flow は次の一本とする。

1. user intent から authorized action を確定する。
2. 大きな context scan が見込まれる場合は先に Site route を確認し、context と必要な Simulator / Site evidence を一括取得する。
3. source、target、identity、cost、verification、observation を決める。
4. prepare / create が必要な場合だけ source から stage を作り、必要な mutation を行う。科学的意味の推定は Simulator capability に委ねる。
5. create が許可されていれば complete stage を no-replace で promote する。
6. execute / submit が許可されていれば、live target に対する validation、command、resource を確認する。
7. Site の live host / route と、retry / restart では active / unresolved Attempt がないことを確認する。
8. Site capability の submit primitive を最大一回呼び、accepted / rejected / unknown を報告し、可能なら記録する。
9. O0–O2 に従って観察し、target、job identity、未解決 warning を返す。

prepare-only なら scratch で、create-only なら promotion で停止する。
payload execution または大きな I/O の直前には live host / route を確認する。

## 5.2 Batch

batch は Agent が single-point workflow をまとめる。

- context、source inspection、scheduler query は可能な限り batch 化する。
- requested point だけを stage し、Survey object や候補 directory を作らない。
- 各 point は許可され、該当する stage、promote、validate、submit だけを独立して行う。
- point 固有 failure は failed / conflict として、独立な後続を続けてよい。
- shared prerequisite failure は未着手 point を skipped として停止する。
- 既に作成した Run や accepted job は自動 rollback しない。

## 5.3 Retry and restart

scheduler-only difference または同じ trajectory の continuation は同じ Run を使う。
retry / restart 前に live scheduler evidence と利用できる local record を照合する。
active / unresolved Attempt がある場合、または照合できない場合は自動 submit しない。

identity が different なら新しい Run、unknown なら確認または停止とする。
timeout のたびに suffix directory を増やさない。

## 5.4 Smoke, pilot, and analysis

smoke、pilot、debug、writable intermediate を作る analysis、large I/O、detached execution は scratch で行う。
bounded かつ read-only で intermediate を作らない analysis は、Site route が許す範囲で直接実行できる。
scratch task は production Run list に混ぜない。
Site scheduler を使う scratch payload にも、一 submit action 一 call と unknown の blind retry 禁止を適用する。

smoke evidence は、Simulator capability が current change へ applicable かつ fresh と判定した場合だけ
再利用する。利用できない場合、execute が許可されていれば fresh smoke を行い、そうでなければ
推奨と理由だけを報告する。smoke evidence の不在自体を workflow 全体の hard failure としない。
step 数や domain size を縮めた smoke は、その差分で何を検証できるかを evidence に含める。

production 判断に使った pilot evidence は必要な要約または再現情報を durable artifact へ残す。
複数 Run の横断 analysis も同じ基準で direct / scratch を選ぶ。export は専用 object を作らず、
既存 research tree の適切な場所へ行い、詳細は 7.5 に従う。

更新中の HDF5 等は Simulator capability が consistent read を保証しない限り解析しない。

## 5.5 Later status inspection

status は submission 時の observation とは別 workflow であり、既定は一回の snapshot とする。

1. available submission record、history、log name から job identity 候補を集める。
2. Site plugin で site / cluster ごとに scheduler を batch query する。
3. terminal、failed、unknown の job だけ必要な simulator output を調べる。
4. scheduler state と simulator outcome を分けて報告する。

---

# 6. Verification and observation

## 6.1 Verification profiles

| Profile | Evidence | Typical use |
|---|---|---|
| V0 Context | source、target、identity、precedent | 全 workflow |
| V1 Static | parse、diff、file / executable / job consistency | routine production |
| V2 Smoke | bounded runtime behavior | new build、runtime、launcher、decomposition |
| V3 Pilot | small-scale scientific behavior / cost | unknown parameter range、大規模展開前 |

create-only は V1 を必須としない。RunHand が scientific mutation を行った production payload は、
execute / submit 直前に live target へ V1 を適用する。明確な invalid は実行を止めるが、
validator unavailable / inconclusive は success に丸めず unvalidated / unknown と報告する。
すでに用意された Run の明示的な execute / submit は、validator 不在だけを理由に一律禁止しない。
V2 / V3 は V1 の代用ではなく追加 evidence である。
V2 / V3 を推奨する場合でも execute が許可されていなければ実行せず、推奨と理由を報告する。

次をすべて満たす routine change は通常 V1 でよい。

- successful source がある
- known parameter の限定 diff
- build、job structure、parallel decomposition が同じ
- static validation が成功
- sibling precedent が十分

new build、初めての setting、boundary / solver / restart の構造変更、launcher / decomposition
変更、過去に early abort した条件では V2 を選ぶ。ただし、Simulator capability が
current change へ applicable かつ fresh と判定した V2 evidence は再利用できる。

未知 parameter range を大量展開する前や production cost が大きい場合は V3 を検討する。

## 6.2 Observation modes

| Mode | Behavior | Typical use |
|---|---|---|
| O0 Detached | accepted Job ID を得たら追加 wait なしで返す | routine production |
| O1 Early | startup だけ bounded に確認する | startup risk が高い production |
| O2 Bounded completion | budget 内で terminal evidence を待つ | smoke、short test、result-dependent analysis |

verification と observation は独立に決める。new build だから常に O1 とは限らず、
V2 が必要だから completion まで production job を待つわけでもない。

O1 は submit 後に一回 scheduler snapshot を取る。

- PENDING: pending_startup_unobserved として直ちに返す。
- RUNNING + startup marker: startup_healthy。
- fatal evidence: startup_failed。
- status unavailable: accepted_unobserved。

PENDING から RUNNING になるまで polling しない。

O2 の既定 budget は 10 分である。超過時は still_running_detached として返し、
completion success と表現しない。user がより長い wait を明示した場合は、その bounded
request を優先するが、background daemon は作らない。

---

# 7. Data, execution, and recovery rules

## 7.1 Context and copy plan

context は workspace、candidate Run、naming pattern、control file、README、prune hint、
recent history、scratch / cache status を一回の scan で返す。

candidate は path だけでなく reason と available evidence を持つ。generic CLI が見つける
candidate は構造候補であり、Simulator evidence がなければ runnable / successful を主張しない。
scan limit、permission、I/O error で不完全なら partial=true と欠落理由を返す。

copy selection は source、include、exclude、symlink policy、completeness を持つ。
CLI へ渡すときは versioned copy plan として表現できるが、独立した永続 object にしない。
Simulator evidence、明示的な user selection、または precedent から導いた場合は根拠と completeness を報告する。
plan と command の source が違う場合は copy を始めない。include / exclude の厳密な pattern grammar は
schema で versioning する。

既定では source 内で完結する relative symlink だけを許す。external / dangling symlink、
FIFO、socket、device 等は、必要性と到達性が明示検証されない限り拒否する。

## 7.2 Stage and promotion

working stage は mutation 可能である。inspect は copy selection、stage state、known warning を返す。
stage の作成と変更は source と formal target を変更しない。

promotion は target parent 上の temporary sibling に complete tree を構築し、target が file、directory、
symlink のいずれとして存在しても上書きせず、原子的な no-replace 操作で公開する。
validation evidence は promotion の必須条件にしない。

disk full、permission error、interrupt、concurrent promotion の失敗時に partial target を正式名で残さない。
同じ target への concurrent promotion は最大一つだけ成功する。

promotion 後に submit が失敗しても、formal Run を自動削除しない。

## 7.3 Execution, submission, and recovery

execute / submit の直前に live target、execution-relevant input、command / job script、resource、
host / route を確認する。Simulator validation が利用であれば、対象とした input revision、
profile、result、validator identity / version を evidence として返す。

revision は、過去の validation が current input に適用できるかの確認と provenance にだけ使う。
Simulator は対象 file の digest、build ID、checkpoint identity 等の実装を選べる。RunHand は output、
log、cache、Run tree 全体の hash を要求しない。過去の evidence が current input へ適用できない
場合は fresh validation を行うか、unvalidated / unknown として報告する。

一つの submit action を処理する間、Site の submit primitive を呼ぶ回数は最大一回とする。retry / restart では、
live evidence で active / unresolved Attempt がないと確認できた場合だけ自動 submit する。

reconciliation key は submit response 前から client が知っており、queue / accounting から一意に検索できる
token または同等の evidence である。response で初めて得る Job ID は unknown 照合能力に含めない。
cross-Agent duplicate protection は provider idempotency があり、Agent layer が同一 submit action を扱う
全 caller へ同じ stable key を渡せる場合だけ保証する。後日の明示 retry / restart は別 action として
新しい key を使う。条件を満たさない場合は duplicate_protection=best_effort と報告する。

accepted、rejected、unknown の意味は次のとおりである。

| Result | Meaning | Next action |
|---|---|---|
| accepted | scheduler が job identity を返した | O0–O2 または later status |
| rejected | 非受理が確定した | error を報告 |
| unknown | timeout / connection loss 等で受理有無が不明 | reconciliation key / live evidence があれば reconcile、なければ unknown_unresolved を報告 |

unknown を rejected とみなして再投入しない。active または unresolved Attempt がある間も
追加 submit しない。Site / Simulator が actual input identity、immutable snapshot、job-start confirmation を
安価に提供する場合は provenance または追加保証として利用できる。これらの不在だけを
理由に既定の submit を拒否しない。より強い frozen-input 保証は optional extension とする。

submission record は作成できる場合に記録する。記録不能だけを理由に明示的な一回の submit を
拒否しないが、recovery と cross-process duplicate protection が制限されることを事前に警告する。

process interruption 後は次を区別する。

- stage only: formal tree は無変更
- promoted, not submitted: complete target は保持
- submit may have started: 照合可能なら reconcile、できなければ unknown_unresolved
- accepted: job identity が返却済みまたは記録済みなら target とともに保持

後続 failure の rollback として accepted job を cancel しない。

## 7.4 Scratch and GC

scratch task は task key、last-used time、pin、active / terminal evidence を持てる。
task key は検索 hint にすぎない。Simulator / Site evidence で同一 task と判定でき、active writer が
ある場合は existing / busy を返して第二 writer を開始しない。terminal task の evidence が current request へ
applicable かつ fresh な場合は結果を再利用できる。同一性が unknown なら別の unique task を作る。

runhand gc は既定で候補と見込み容量だけを表示する。--apply でのみ削除する。
対象は RunHand-owned scratch、cache、reconciled history に限定し、次は削除しない。

- formal research tree
- pinned task
- active task
- nonterminal / unresolved Attempt に結び付く task
- liveness や scheduler state を確認できない task

liveness 不明な task は bulk GC から除外する。user が exact task を orphan として明示した場合だけ、
Site での再照合を試みた後に `gc --orphan TASK --apply` で削除できる。

## 7.5 Durable analysis artifacts

durable analysis artifact は既存 project convention に従う通常の Markdown、script、notebook、figure、
table、directory 等である。RunHand 専用 schema、registry、reader を必要としない。
複数 Run を横断する結果を export する場合は、再実行または解釈に必要な範囲で
resolved source、selection、method を artifact または隣接文書に残す。

---

# 8. CLI contract

## 8.1 Position

CLI は deterministic worker であり、科学的判断、parameter semantics、site semantics を持たない。
通常は次の形で利用する。

    runhand <command>

既設 CLI を優先する。temporary installation が必要な場合は exact compatible version を pin し、
Site policy が network access を許す場合だけ行う。

## 8.2 Public command surface

| Command | Purpose |
|---|---|
| runhand context | workspace と local precedent を一括 scan |
| runhand stage create | copy plan から scratch stage を作る |
| runhand stage inspect | copy selection、stage state、warning を返す |
| runhand promote | complete stage を formal target へ atomic no-replace で配置 |
| runhand scratch get / pin / unpin | reusable scratch task を管理 |
| runhand gc | disposable data を preview / delete |
| runhand doctor | version、config、workspace、state / scratch、plugin cues を確認 |

core synopsis:

    runhand [--workspace PATH] [--scratch-root PATH] [--state-root PATH] <command>
    runhand context [PATH] [--refresh] [--json]
    runhand stage create --source PATH --plan FILE|- [--dry-run] [--json]
    runhand stage inspect STAGE [--json]
    runhand promote STAGE TARGET [--dry-run] [--json]
    runhand scratch get --kind smoke|pilot|debug|analysis|preview --key TEXT [--pin] [--dry-run] [--json]
    runhand scratch pin TASK [--dry-run] [--json]
    runhand scratch unpin TASK [--dry-run] [--json]
    runhand gc [--kind scratch|cache|history|all] [--older-than DURATION] [--apply] [--json]
    runhand gc --orphan TASK [--apply] [--json]
    runhand doctor [--json]

submission record と Site-provided submission mechanism は user-facing project object にしない。
versioned provider contract は accepted / rejected / unknown、job evidence、unknown 照合用の optional
pre-submit reconciliation key、重複排除用の optional provider idempotency と key scope を区別する。
記録済みの unresolved state は context / doctor の warning から確認できなければならない。

scratch task key は検索 hint であり、その一致だけを reuse の根拠にしない。
runtime evidence の適用可否は Simulator capability が判断する。
copy plan は `--plan -` で stdin から渡せ、中間 file の作成を必須としない。

## 8.3 JSON and dry-run

--json の stdout は成功・失敗とも UTF-8 JSON object 一つだけとする。診断 log は stderr に出す。

    {
      "schema": 1,
      "ok": true,
      "command": "context",
      "data": {},
      "warnings": [],
      "errors": []
    }

warning / error は stable code、message、retryable、必要なら canonical path と details を持つ。
ok=true は exit code 0 の場合だけとする。human-readable output と JSON は同じ結果を表す。

GC 以外の全 mutating command は --dry-run を受け付け、RunHand-managed filesystem、cache、
history、optional submission record、external service を変更しない。GC は --apply がない状態を preview とする。

context、copy plan、validation evidence、provider request / response の schema は schemas/v1 に置き、
同一 major version 内で後方互換にする。該当 schema を同梱する前に関連 command または
provider contract の 1.0 conformance を主張しない。

## 8.4 Exit codes

| Code | Meaning |
|---:|---|
| 0 | success |
| 1 | unexpected internal error |
| 2 | usage / configuration error |
| 3 | ambiguity / collision / unsafe target |
| 4 | plan / validation precondition failure |
| 5 | filesystem / local state I/O failure |
| 6 | provider compatibility / requested integration failure |

CLI Core は batch transaction を持たないため、partial batch exit code は定義しない。

---

# 9. Configuration and non-functional requirements

## 9.1 Configuration precedence

同じ key は弱い順に次を merge する。

1. built-in default
2. Site-recommended default
3. global config
4. workspace runhand.toml
5. documented environment variable
6. CLI argument

Site の強制 safety constraint は merge layer ではなく、payload の直前に適用する
override 不可の execution constraint である。
user prompt は config layer ではなく、action authorization と preference を与える。
unsupported major、invalid type / range、矛盾した config は exit code 2 とし、一部適用しない。

scratch root が未指定なら Site-recommended scratch、次に XDG cache、最後に workspace 内の
RunHand-owned hidden area を候補にする。state root が未指定なら XDG state を候補にする。
cross-control-host duplicate protection は Site-provided idempotency と同一 submit action の shared stable key が
ある場合だけ保証し、なければその限界を報告する。

minimal config:

    version = 1

    [behavior]
    verification = "adaptive"
    observation = "adaptive"

    [observation]
    completion_budget_seconds = 600

    [scratch]
    # root = "/site/appropriate/path"
    ttl_days = 14
    history_ttl_days = 90
    max_gib = 50

    [state]
    # root = "/shared/durable/path"

    [context]
    max_depth = 8
    warm_cache = true

## 9.2 Performance

- context は repeated pwd / find / ls を一回の bounded scan に統合する。
- output directory の全再帰 scan と全 hash を既定で行わない。
- scheduler status は job ごとではなく site / cluster ごとの batch query を優先する。
- O0 は Job ID 取得後に sleep または status polling を追加しない。
- cache lock 競合時は read-only cache または uncached bounded scan へ fallback する。

reference fixture 上の目標は warm context 約 1 秒、prune hint のある 10,000 directory の
cold context 約 5 秒とする。これは filesystem と hardware 条件を記録する SLO であり、
任意の HPC filesystem に対する portable MUST ではない。

## 9.3 Compatibility and persistence

- primary OS は Linux / HPC environment とする。
- RunHand CLI 1.0 の最低 Python は 3.11 とする。
- core dependency は小さく保ち、offline / cached installation を優先する。
- cache corruption は live rescan で回復する。
- cache / history failure は scientific result を失わせない。
- optional submission record を保存できない場合は警告し、recovery や duplicate protection を主張しない。
- RunHand を削除しても Run、input、output、README、durable analysis artifact を通常 tool で利用できる。

---

# 10. Acceptance criteria

各 test は fixture、config / cache state、Simulator / Site mock、user intent を固定し、
exit code、JSON、filesystem diff、provider call count を観測する。

| ID | Verifies | Scenario and expected result |
|---|---|---|
| AC-01 | RH-01, RH-02, RH-07, RH-08, RH-09, RH-12, RH-16 | unique precedent から known parameter を「作って流して」。不要な確認なし、context scan 一回で mutation、atomic no-replace create、live validation / Site route 確認、scheduler call 一回を行い、O0 で target と job identity を返す。 |
| AC-02 | RH-01, RH-07, RH-08 | validator unavailable で明示 copy selection を「Run にして」。complete target は作成され、validation=unvalidated、scheduler call は 0。「smoke して」では bounded scratch Attempt だけを許可し、production submit / export は 0。 |
| AC-03 | RH-02, RH-03, RH-07 | 既存 Run と exact job script を指定して submit する。Simulator 不在でも validation=unavailable の warning とともに scheduler call は一回。Site 不在、route unknown、禁止 host では scheduler call は 0。 |
| AC-04 | RH-03, RH-05 | identity=unknown でも別の明示 target へは warning 付きで create できる。既存 Run の reuse / retry は判定に必要な evidence が得られるまで行わない。 |
| AC-05 | RH-04, RH-15 | malicious README / job script を含む context scan でも process spawn は 0。同じ snapshot の二回の結果順は同じで、budget 超過は partial=true と reason を返す。 |
| AC-06 | RH-06 | create 前後で source は不変。unsafe symlink、special file、copy 中の source inconsistency を検出した場合は失敗し、formal target を残さない。 |
| AC-07 | RH-08 | target が file、directory、symlink のいずれかとして存在する場合は上書きしない。concurrent create は最大一つだけ成功し、disk full、permission error、interrupt でも partial target を正式名で残さない。 |
| AC-08 | RH-07, RH-11 | 過去の validation 後に execution-relevant input を変更する。submit 直前に live target へ validation を再適用し、明確な invalid なら scheduler call は 0。output や Run tree 全体の hash は判定条件にしない。 |
| AC-09 | RH-09 | Site が provider idempotency を提供し、Agent layer が同一 submit action の concurrent caller へ同じ key を渡すと accepted Attempt は最大一つ。後日の明示 retry は別 key になる。いずれかを提供できない場合は明示 submit を妨げず submit primitive を一回呼び、duplicate_protection=best_effort を返す。 |
| AC-10 | RH-09, RH-10 | scheduler が受理後に応答を失う fixture で submit primitive call は一回、result は unknown、自動 retry は 0。pre-submit reconciliation key ありなら live evidence で照合し、なしなら unknown_unresolved を返す。submission record が書けなくても最初の明示 submit は禁止せず、recovery_unavailable を返す。 |
| AC-11 | RH-10 | retry / restart 前の live query で active Attempt が見つかるか、query 自体が失敗した場合、submit primitive call は 0。後者は unknown と返し、success / rejected と表現しない。 |
| AC-12 | RH-02, RH-11 | login node fixture の smoke / analysis / large I/O は local heavy payload 0 で Site route を使う。既定 workflow は immutable snapshot や RunHand 固有の job-start revision check を要求しない。 |
| AC-13 | RH-03, RH-12 | O0 accepted production は polling 0。O1 PENDING は snapshot 一回、sleep 0 で pending_startup_unobserved。O2 が budget 内に terminal evidence を得なければ still_running_detached とし、success と表現しない。 |
| AC-14 | RH-13 | batch の一 point が create または validation failure でも独立な後続を処理し、既存 Run / accepted job を rollback しない。 |
| AC-15 | RH-14 | bulk GC preview は削除 0。--apply でも formal、active、pinned、nonterminal、unresolved、liveness 不明な task を残す。exact orphan task は Site 再照合後の `--orphan TASK --apply` でだけ削除する。 |
| AC-16 | RH-15 | --json usage error は object 一つ、ok=false、exit 2。filesystem mutating command の dry-run 前後で source、target、managed state は同一。 |
| AC-17 | RH-17 | 複数 Run の bounded read-only analysis は scratch 作成なし、writable intermediate を要する analysis は scratch で行う。export した artifact は専用 reader なしで resolved source、selection、method を確認でき、RunHand 削除後も利用できる。secret fixture は state / JSON で redacted する。 |
| AC-18 | RH-15 | config は CLI > env > workspace > global > Site default > built-in で解決し、invalid config は exit 2 で一部適用しない。 |

---

# Glossary

| Term | Meaning |
|---|---|
| Run | Simulator が一つの simulation 単位と認識する directory |
| Simulation identity | Simulator が結果を区別する semantic identity |
| Attempt | Run または scratch task に対する一回の scheduler submission |
| Workspace | 現在作業する通常の research directory tree |
| Scratch | stage、test、analysis 用の disposable area |
| Precedent | nearby Run、naming、README、recent activity から得る local evidence |
| Stage | formal target へ配置する前の working copy |
| Promote | complete stage を formal target へ atomic no-replace で配置する操作 |
| V0–V3 | verification profile |
| O0–O2 | observation mode |
| Unknown | evidence が足りず、success / failure に丸めない状態 |
