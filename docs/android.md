# FinPulse Android

Android 工程位于 `android/`，使用原生 Java Activity + 系统 WebView，Android 8.0（API 26）及以上可安装。它连接现有 FastAPI 网页，账号、CSRF、Cookie 与 API 保持同源；不将后端、数据库或 API 密钥打入 APK。正式版固定连接 `http://8.148.79.227:16800/`，顶部仅保留品牌与相邻的圆形箭头刷新按钮，旧版保存的服务器地址不再生效。v1.0.2 改用公网入口，升级时跳过旧入口的页面状态和浏览历史；新地址首次使用需重新登录。

手机通过公网入口访问服务器。APK 不改变服务器的网络可达范围。网页由服务器提供，网页更新自动生效；离线不能使用行情和账号功能。系统 WebView 应保持更新。

## 构建与检查

安装 JDK 17 和 Android SDK（platforms;android-35、build-tools;35.0.0），设置 `ANDROID_HOME` 后：

```bash
cd android
./gradlew :app:assembleDebug :app:assembleDebugAndroidTest :app:lintRelease
```

Debug APK 只用于本地检查。正式包需提供环境变量 `FINPULSE_KEYSTORE`（绝对路径）、`FINPULSE_STORE_PASSWORD`、`FINPULSE_KEY_ALIAS`、`FINPULSE_KEY_PASSWORD`，执行 `./gradlew :app:assembleRelease`。无签名配置时 Gradle 生成 unsigned 包，不能用于发布。

Gradle Wrapper 固定为 8.11.1，校验下载 SHA-256；AGP 固定为 8.9.2。原生工程不依赖 React 构建或本机 `.env`。网页仍由现有服务器部署流程构建与提供。

## GitHub Release

`.github/workflows/android-release.yml` 在 `codex/android-apk-release` 分支推送或手动触发时验证，在推送 `android-v*` 标签时先验证，再发布签名 APK 和 SHA-256。验证包括 lint 和 Android 15 模拟器：启动真实 WebView，以隔离的本地 HTTP fixture 检查 JavaScript、同源 Cookie 请求、localStorage、顶部图标刷新、JSON 导出字节和文件名、带 Cookie 的 CSV 下载、文件选择器取消及重试、外链浏览器跳转及地址验证；不使用生产账号或修改服务器数据。它不是所有业务界面的端到端测试。

仓库需配置以下 GitHub Actions secrets：

- `FINPULSE_KEYSTORE_BASE64`：专用 release keystore 的 Base64。
- `FINPULSE_STORE_PASSWORD` 和 `FINPULSE_KEY_PASSWORD`：对应密码。

工作流内固定别名为 `finpulse`。本次生成的私有密钥保存在发布开发机 `~/.local/share/finpulse/android-signing/`；不得上传仓库或 Release，应由维护者安全备份。后续版本必须使用同一密钥，递增 `android/app/build.gradle` 的 `versionCode`、`versionName` 并更新发布说明。丢失密钥将无法覆盖安装已有应用。

调试构建仅为隔离测试读取 `finpulse-test` 偏好中的回环地址（127.0.0.1），正式构建不读取任何服务器偏好。

## 客户端行为

- JavaScript 和 DOM storage 启用，第三方 Cookie、文件系统访问和混合内容禁用；文件选择器仅授予用户选择的 content URI。
- 不暴露 JavaScript/Java bridge，不忽略 HTTPS 证书错误；站外 HTTP(S) 新闻链接交给外部浏览器。
- 当前服务器使用 HTTP，因此应用允许明文网络连接；正式版没有服务器选择或自定义地址入口。HTTPS 链接遵循系统证书验证。
- JSON Blob 导出由页面注入脚本转为 Base64，通过私有导航 scheme 交给系统保存选择器；仅接受来自当前服务器页面的导出，最多16MB。
- CSV 等同源 URL 下载携带当前服务器 Cookie，不跟随重定向，交给系统保存选择器；无需广泛存储权限。
- Release 包禁止 WebView 调试，禁止 Android 自动备份；签名后由 `apksigner verify` 校验。
