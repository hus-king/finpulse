package cn.finpulse.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.util.TypedValue;
import android.widget.ImageButton;
import android.net.Uri;
import android.os.Bundle;
import android.util.Base64;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.util.Locale;

public class MainActivity extends Activity {
    static final String PUBLIC_SERVER = "http://8.148.79.227:16800/";
    private static final int PICK_FILE = 10, SAVE_FILE = 11, MAX_EXPORT = 16 * 1024 * 1024;
    private WebView web;
    private ProgressBar progress;
    private LinearLayout errorPanel;
    private TextView errorText;
    private String server = PUBLIC_SERVER;
    private boolean pageFailed;
    private ValueCallback<Uri[]> fileCallback;
    private byte[] pendingExport;
    private long backPressedAt;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        // Release builds always use the public server, including upgrades with old saved settings.
        if (BuildConfig.DEBUG) {
            String fixture = getSharedPreferences("finpulse-test", MODE_PRIVATE).getString("server", "");
            try {
                String normalized = normalizeServer(fixture);
                if ("127.0.0.1".equals(URI.create(normalized).getHost())) server = normalized;
            } catch (IllegalArgumentException ignored) { /* Use the public server. */ }
        }
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(9, 12, 18));
        // Android 15 draws edge-to-edge: keep the toolbar and web content within system bars.
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            v.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                    insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets.consumeSystemWindowInsets();
        });
        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setGravity(android.view.Gravity.CENTER_VERTICAL);
        TextView title = new TextView(this);
        toolbar.setPadding(dp(16), 0, dp(12), 0);
        title.setText("FinPulse"); title.setTextColor(Color.WHITE); title.setTextSize(18);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        title.setGravity(android.view.Gravity.CENTER_VERTICAL);
        toolbar.addView(title, new LinearLayout.LayoutParams(-2, -1));
        ImageButton refresh = new ImageButton(this);
        refresh.setImageResource(R.drawable.ic_refresh);
        refresh.setImageTintList(android.content.res.ColorStateList.valueOf(Color.rgb(177, 189, 207)));
        refresh.setPadding(dp(11), dp(11), dp(11), dp(11));
        refresh.setContentDescription("刷新页面"); refresh.setTooltipText("刷新页面");
        refresh.setTag("refresh-page");
        TypedValue ripple = new TypedValue();
        getTheme().resolveAttribute(android.R.attr.selectableItemBackgroundBorderless, ripple, true);
        refresh.setBackgroundResource(ripple.resourceId);
        refresh.setOnClickListener(v -> { if (pageFailed) loadHome(); else web.reload(); });
        LinearLayout.LayoutParams refreshLayout = new LinearLayout.LayoutParams(dp(44), dp(44));
        refreshLayout.leftMargin = dp(6);
        toolbar.addView(refresh, refreshLayout);
        root.addView(toolbar, new LinearLayout.LayoutParams(-1, dp(48)));
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        root.addView(progress, new LinearLayout.LayoutParams(-1, dp(3)));
        errorPanel = new LinearLayout(this);
        errorPanel.setOrientation(LinearLayout.VERTICAL); errorPanel.setPadding(dp(24), dp(36), dp(24), dp(24));
        errorText = new TextView(this); errorText.setTextSize(17); errorText.setTextColor(Color.WHITE);
        errorPanel.addView(errorText);
        errorPanel.addView(button("重试", v -> loadHome()));
        errorPanel.setVisibility(View.GONE);
        root.addView(errorPanel, new LinearLayout.LayoutParams(-1, 0, 1));
        web = new WebView(this);
        root.addView(web, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
        configureWeb();
        if (state == null || !server.equals(state.getString("finpulse-server"))
                || web.restoreState(state) == null || !sameOrigin(web.getUrl())) loadHome();
    }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private Button button(String label, View.OnClickListener listener) {
        Button b = new Button(this); b.setText(label); b.setAllCaps(false); b.setOnClickListener(listener); return b;
    }
    static String normalizeServer(String value) {
        URI u = URI.create(value.trim());
        String scheme = u.getScheme() == null ? "" : u.getScheme().toLowerCase(Locale.ROOT);
        if (!(scheme.equals("http") || scheme.equals("https")) || u.getHost() == null
                || u.getRawUserInfo() != null || u.getRawQuery() != null || u.getRawFragment() != null
                || !(u.getPath().isEmpty() || u.getPath().equals("/")) || u.getPort() > 65535 || u.getPort() == 0)
            throw new IllegalArgumentException("请填写 http:// 或 https:// 开头的服务器根地址，不含路径、账号或参数");
        return scheme + "://" + u.getRawAuthority() + "/";
    }
    private static int port(URI u) { return u.getPort() >= 0 ? u.getPort() : ("https".equalsIgnoreCase(u.getScheme()) ? 443 : 80); }
    private boolean sameOrigin(String url) {
        try {
            URI a = URI.create(server), b = URI.create(url);
            return a.getScheme().equalsIgnoreCase(b.getScheme()) && a.getHost().equalsIgnoreCase(b.getHost())
                    && port(a) == port(b) && b.getRawUserInfo() == null;
        } catch (Exception e) { return false; }
    }
    @SuppressLint("SetJavaScriptEnabled") private void configureWeb() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true); s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false); s.setAllowContentAccess(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setSupportMultipleWindows(false);
        s.setUserAgentString(s.getUserAgentString() + " FinPulseAndroid/" + BuildConfig.VERSION_NAME);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                if (url.startsWith("finpulse-export:")) {
                    if (request.isForMainFrame() && sameOrigin(view.getUrl())) receiveBlob(request.getUrl());
                    return true;
                }
                if (sameOrigin(url)) return false;
                if (request.isForMainFrame()) openExternal(url);
                return true;
            }
            @Override public void onPageStarted(WebView v, String url, android.graphics.Bitmap icon) {
                pageFailed = false; progress.setVisibility(View.VISIBLE);
            }
            @Override public void onPageFinished(WebView v, String url) {
                CookieManager.getInstance().flush();
                if (!pageFailed && sameOrigin(url)) {
                    installExportSupport();
                    web.setVisibility(View.VISIBLE); errorPanel.setVisibility(View.GONE);
                }
            }
            @Override public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
                if (r.isForMainFrame()) fail("无法连接服务器，请检查网络后重试。");
            }
            @Override public void onReceivedHttpError(WebView v, WebResourceRequest r, WebResourceResponse e) {
                if (r.isForMainFrame()) fail("服务器返回 HTTP " + e.getStatusCode() + "。请稍后重试。");
            }
            @Override public void onReceivedSslError(WebView v, android.webkit.SslErrorHandler handler, android.net.http.SslError error) {
                handler.cancel(); fail("服务器 HTTPS 证书无效，请联系管理员。");
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onProgressChanged(WebView v, int percent) {
                progress.setProgress(percent); progress.setVisibility(percent == 100 ? View.GONE : View.VISIBLE);
            }
            @Override public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*");
                intent.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{"application/json", "text/plain", "application/octet-stream"});
                try { startActivityForResult(intent, PICK_FILE); }
                catch (ActivityNotFoundException e) { fileCallback.onReceiveValue(null); fileCallback = null; toast("未找到文件选择器"); }
                return true;
            }
        });
        web.setDownloadListener((url, userAgent, disposition, mime, length) -> {
            if (url.startsWith("blob:")) exportBlob(url, "finpulse-export.json");
            else if (sameOrigin(url)) download(url, mime);
            else openExternal(url);
        });
    }
    private void loadHome() {
        pageFailed = false; errorPanel.setVisibility(View.GONE); web.setVisibility(View.VISIBLE); web.loadUrl(server);
    }
    private void fail(String message) {
        pageFailed = true; web.setVisibility(View.GONE); progress.setVisibility(View.GONE);
        errorText.setText(message + "\n\n当前地址：" + server); errorPanel.setVisibility(View.VISIBLE);
    }
    private void openExternal(String url) {
        Uri u = Uri.parse(url);
        if (!("http".equalsIgnoreCase(u.getScheme()) || "https".equalsIgnoreCase(u.getScheme()) || "mailto".equalsIgnoreCase(u.getScheme()))) {
            toast("不支持此链接类型"); return;
        }
        try { startActivity(new Intent(Intent.ACTION_VIEW, u).addCategory(Intent.CATEGORY_BROWSABLE)); }
        catch (ActivityNotFoundException e) { toast("未找到可以打开此链接的应用"); }
    }
    // No JavaScript/Java bridge: the page sends export data through a private navigation scheme.
    private static final String EXPORT_FUNCTION = "function(u,n){fetch(u).then(r=>r.blob()).then(b=>{if(b.size>16777216)throw Error('文件超过16MB');let f=new FileReader();f.onload=()=>{location.href='finpulse-export://save?name='+encodeURIComponent(n||'finpulse-export.json')+'&data='+encodeURIComponent(f.result)};f.readAsDataURL(b)}).catch(e=>alert('导出失败：'+e.message))}";
    private void installExportSupport() {
        web.evaluateJavascript("(()=>{if(window.__finpulseExport)return;window.__finpulseExport=" + EXPORT_FUNCTION + ";const c=HTMLAnchorElement.prototype.click;HTMLAnchorElement.prototype.click=function(){if(this.href.startsWith('blob:'))window.__finpulseExport(this.href,this.download);else c.call(this)}})()", null);
    }
    private void exportBlob(String url, String name) {
        if (sameOrigin(web.getUrl())) web.evaluateJavascript("(" + EXPORT_FUNCTION + ")(" + org.json.JSONObject.quote(url) + "," + org.json.JSONObject.quote(name) + ")", null);
    }
    private void receiveBlob(Uri uri) {
        try {
            String data = uri.getQueryParameter("data");
            if (data == null || data.length() > MAX_EXPORT * 2 || !data.startsWith("data:") || !data.substring(0, data.indexOf(',')).endsWith(";base64")) throw new Exception();
            byte[] bytes = Base64.decode(data.substring(data.indexOf(',') + 1), Base64.DEFAULT);
            if (bytes.length > MAX_EXPORT) throw new Exception();
            save(bytes, uri.getQueryParameter("name"), "application/json");
        } catch (Exception e) { toast("导出失败或文件超过 16 MB"); }
    }
    private void download(String url, String mime) {
        String cookie = CookieManager.getInstance().getCookie(url);
        String name = Uri.parse(url).getLastPathSegment();
        new Thread(() -> {
            HttpURLConnection connection = null;
            try {
                connection = (HttpURLConnection) new URL(url).openConnection();
                connection.setInstanceFollowRedirects(false); connection.setConnectTimeout(15000); connection.setReadTimeout(30000);
                if (cookie != null) connection.setRequestProperty("Cookie", cookie);
                connection.setRequestProperty("User-Agent", "FinPulseAndroid/" + BuildConfig.VERSION_NAME);
                if (connection.getResponseCode() != 200) throw new Exception();
                byte[] bytes;
                try (InputStream in = connection.getInputStream(); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
                    byte[] chunk = new byte[8192]; int count;
                    while ((count = in.read(chunk)) != -1) { if (out.size() + count > MAX_EXPORT) throw new Exception(); out.write(chunk, 0, count); }
                    bytes = out.toByteArray();
                }
                runOnUiThread(() -> { if (!isFinishing() && !isDestroyed()) save(bytes, name, mime); });
            } catch (Exception e) { runOnUiThread(() -> toast("下载失败，请检查登录状态、网络和文件大小（最多16MB）")); }
            finally { if (connection != null) connection.disconnect(); }
        }, "finpulse-download").start();
    }
    private void save(byte[] bytes, String name, String mime) {
        if (pendingExport != null) { toast("请先完成当前文件的保存"); return; }
        pendingExport = bytes;
        String safeName = name == null ? "finpulse-export.json" : name.replaceAll("[^\\p{L}\\p{N}._-]", "_");
        if (safeName.length() > 120) safeName = safeName.substring(safeName.length() - 120);
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                .setType(mime == null || mime.isEmpty() ? "application/octet-stream" : mime).putExtra(Intent.EXTRA_TITLE, safeName);
        try { startActivityForResult(intent, SAVE_FILE); }
        catch (ActivityNotFoundException e) { pendingExport = null; toast("未找到文件保存应用"); }
    }
    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == PICK_FILE && fileCallback != null) {
            fileCallback.onReceiveValue(result == RESULT_OK && data != null && data.getData() != null ? new Uri[]{data.getData()} : null); fileCallback = null;
        }
        if (request == SAVE_FILE) {
            byte[] bytes = pendingExport; pendingExport = null;
            if (result == RESULT_OK && data != null && data.getData() != null && bytes != null) {
                Uri target = data.getData();
                new Thread(() -> {
                    try (OutputStream out = getContentResolver().openOutputStream(target)) {
                        if (out == null) throw new Exception(); out.write(bytes);
                        runOnUiThread(() -> toast("文件已保存"));
                    } catch (Exception e) { runOnUiThread(() -> toast("保存失败，请重试")); }
                }, "finpulse-save").start();
            }
        }
    }
    private void toast(String text) { Toast.makeText(this, text, Toast.LENGTH_LONG).show(); }
    @Override protected void onSaveInstanceState(Bundle state) { state.putString("finpulse-server", server); web.saveState(state); super.onSaveInstanceState(state); }
    @Override public void onBackPressed() {
        if (!pageFailed && web.canGoBack()) { web.goBack(); return; }
        long now = android.os.SystemClock.elapsedRealtime();
        if (now - backPressedAt < 2000) super.onBackPressed();
        else { backPressedAt = now; toast("再按一次返回键退出"); }
    }
    @Override protected void onPause() { CookieManager.getInstance().flush(); web.onPause(); super.onPause(); }
    @Override protected void onResume() { super.onResume(); if (web != null) web.onResume(); }
    @Override protected void onDestroy() {
        if (fileCallback != null) fileCallback.onReceiveValue(null);
        web.stopLoading(); web.destroy(); super.onDestroy();
    }
}
