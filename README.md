# LoRA Library Desk

Local LoRA/checkpoint library with a Chrome download bridge and per-model Forge Neo generation settings.

**Windows向け試用版 / 0.1.0-alpha.1 / MIT**

LoRA・チェックポイント・Embedding・ComfyUI Workflowを、プレビュー・Trigger・カテゴリ・星評価・メモで整理するローカルアプリです。モデルのフォルダを登録して使います。

![個人データを含まない初回起動・接続設定画面](docs/images/library-desk.png)

## 必要なもの

- Windows 10/11、Python 3.10以上（本体は標準ライブラリのみ。pipインストール不要）
- Chrome/Chromium系ブラウザ（Chrome拡張を使う場合）
- Forge Neo（生成設定の連携を使う場合）

モデルファイル・画像・Python・Forgeは同梱しません。

## 起動

1. [Releases](https://github.com/gariyou/lora-library-desk/releases)からZIPをダウンロードし、書き込み可能なフォルダへ展開します。
2. `start.bat`をダブルクリックします。Pythonが見つからなければPython公式インストーラーで導入してください。
3. `http://127.0.0.1:8787/lora`が開きます。管理するモデルのフォルダを登録してください。

手動起動：`py -3 server.py`。停止は起動した端末で`Ctrl+C`です。
ポート変更：`py -3 server.py --port 8877`。既に使われているポートのプロセスは自動停止しません。

管理データは展開先の`data/`に初回起動時から保存されます。更新時はアプリを停止し、`data/`をバックアップして残したままプログラムを置き換えてください。保存済みのパスが変わると別モデルとして扱われます。

## Chrome拡張：LoRA Manager Bridge

1. `chrome://extensions`で「デベロッパーモード」をオンにします。
2. 「パッケージ化されていない拡張機能を読み込む」で、このZIP内の`chrome_extension/`を選びます。
3. 本体を起動し、watchフォルダを登録します。拡張の接続先は本体と同じURLにします。
4. Civitaiのモデル配布ページで候補・保存先を確認し、Civitai側の通常ダウンロードを行います。拡張がChromeのダウンロード完了を照合して登録フォルダへ取り込みます。ページを開くと先頭候補で自動待機するため、別候補を使う場合は先に待機を解除してください。

ページ情報だけ送信することもできます。画像ページでは生成情報の取得に対応しています。
認証・有料モデルの購入や権限の取得は行いません。

拡張から本体を起動したい場合だけ、`register_lora_manager_protocol.bat`を実行します。これは現在のユーザーの`lora-manager://`ハンドラーを登録します。通常利用には不要です。

## Forge連携

[Forge Library Desk Bridge](https://github.com/gariyou/sd-forge-library-desk-bridge)をForgeへ導入してください。手動導入用コピーはこのZIPの`forge_bridge/`にもあります。

- Forge画面とLibrary Deskを両方開きます。
- Forgeのtxt2img、Generate下の「現在の設定をLibrary Deskへ保存」で、選択中のチェックポイントに設定を保存します。本体側の「Forgeの設定を保存」でも保存できます。
- 本体でチェックポイントを選び「Forgeへ送る」で内容を確認して復元します。LoRAではタグとTriggerをPromptへ追加します。
- Step、Sampler、Scheduler、CFG、サイズ、Seed、Prompt、Negative Prompt、Hires、Refiner、拡張機能の数値・文字・選択設定、生成関連オプション、外部VAE／エンコーダーをモデル別に保存します。外部モジュールの未選択も保存できます。
- 画像生成は開始しません。ControlNet等の入力画像そのもの、img2img、実行中ジョブは保存対象外です。拡張構成やモデル／モジュールが異なる場合は復元を拒否します。

接続先の初期値はForge=`http://127.0.0.1:7860`、本体=`http://127.0.0.1:8787`です。本体の「Forge連携→接続設定」でForge URLを変更できます。Forge側の`Settings→Library Desk`で本体のURLを変更し、Apply settings→Reload UIでリンクも更新してください。Chrome拡張にも同じ本体URLを設定します。Forge連携の接続先は同じPCのHTTPループバックURLに限ります。

## データと通信

詳細は[PRIVACY.md](PRIVACY.md)。配布物に管理DB・登録フォルダ・モデル・画像・ログ・認証情報は含まれません。起動時のwatchフォルダは空です。

## 対応範囲と開発

WindowsとForge Neo（Neo 2.29.2 / Gradio 4.40.0）で検証しています。A1111、他のForge派生、macOS/Linuxは対応未確認です。公開版の検証内容と限界は[docs/VALIDATION.md](docs/VALIDATION.md)に記載します。

`python -m unittest discover -s tests -p "test_*.py"`

Node.jsだけで実行できる拡張のテスト：`node --test tests/test_auto_wait.cjs tests/test_auto_recovery.cjs tests/test_download_completion.cjs`

一部のブラウザテストはPlaywrightを必要とします。アプリの通常利用にはNode.jsやPlaywrightは不要です。

MITライセンス。外部プロジェクトの扱いは[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。不具合報告ではモデル実体・認証情報・個人プロンプトを添付せず、再現手順とバージョンを記載してください。

## English quick start

Install Python 3.10+, unzip a release, run `start.bat`, then add your model directories at `http://127.0.0.1:8787/lora`. Load `chrome_extension/` as an unpacked Chrome extension. Install the separate Forge bridge to save and restore checkpoint-specific txt2img settings. Windows/Forge Neo only for this preview; generation and model downloads are never started by a settings transfer. User data lives in `data/` and is excluded from distributions.
