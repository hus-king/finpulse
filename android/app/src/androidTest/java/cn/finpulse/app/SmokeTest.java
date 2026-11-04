package cn.finpulse.app;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.os.Bundle;
import android.webkit.WebView;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.concurrent.atomic.AtomicInteger;
import android.net.Uri;
import android.webkit.WebChromeClient;

/** Real WebView integration checks against an isolated HTTP fixture, without production accounts. */
public class SmokeTest extends Instrumentation {
    @Override public void onCreate(Bundle args) { super.onCreate(args); start(); }
    private void check(boolean value, String message) { if (!value) throw new AssertionError(message); }
    private String js(WebView web, String script) throws Exception {
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>();
        runOnMainSync(() -> web.evaluateJavascript(script, value -> { result.set(value); latch.countDown(); }));
        check(latch.await(10, TimeUnit.SECONDS), "JavaScript timed out");
        return result.get();
    }
    @Override public void onStart() {
        Bundle result = new Bundle(); Activity activity = null;
        try (ServerSocket server = new ServerSocket(0)) {
            String origin = "http://127.0.0.1:" + server.getLocalPort() + "/";
            Thread fixture = new Thread(() -> {
                while (!server.isClosed()) {
                    try (Socket socket = server.accept()) {
                        BufferedReader input = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8));
                        String line = input.readLine(); boolean cookie = false;
                        String request = line == null ? "" : line;
                        while ((line = input.readLine()) != null && !line.isEmpty()) if (line.toLowerCase().startsWith("cookie:") && line.contains("session=smoke")) cookie = true;
                        boolean csv = request.contains(" /export.csv ");
                        String html = csv ? (cookie ? "code,name\n600000,FinPulse\n" : "unauthenticated") : request.contains(" /session ") ? (cookie ? "authenticated" : "anonymous")
                                : "<!doctype html><meta name='viewport' content='width=device-width'><title>FinPulse test</title><body><input type='file' accept='application/json'><a id='export'>Export</a><script>localStorage.setItem('smoke','persisted');document.cookie='session=smoke; SameSite=Lax; path=/';window.fixtureReady=true;</script>";
                        byte[] bytes = html.getBytes(StandardCharsets.UTF_8);
                        socket.getOutputStream().write(("HTTP/1.1 200 OK\r\nContent-Type: " + (csv ? "text/csv\r\nContent-Disposition: attachment; filename=export.csv" : "text/html; charset=utf-8") + "\r\nContent-Length: " + bytes.length + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.UTF_8));
                        socket.getOutputStream().write(bytes);
                    } catch (Exception e) { if (!server.isClosed()) throw new RuntimeException(e); }
                }
            }, "smoke-http"); fixture.setDaemon(true); fixture.start();
            check(MainActivity.normalizeServer(origin).equals(origin), "server normalization");
            for (String invalid : new String[]{"file:///tmp", "javascript:alert(1)", "https://user:pass@example.com", "http://example.com/api", "http://example.com?x=1"}) {
                boolean rejected = false; try { MainActivity.normalizeServer(invalid); } catch (Exception e) { rejected = true; }
                check(rejected, "accepted invalid server: " + invalid);
            }
            getTargetContext().getSharedPreferences("finpulse", 0).edit().putString("server", origin).commit();
            Intent intent = new Intent(getTargetContext(), MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            activity = startActivitySync(intent);
            java.lang.reflect.Field field = MainActivity.class.getDeclaredField("web"); field.setAccessible(true);
            WebView web = (WebView) field.get(activity);
            boolean ready = false;
            for (int i = 0; i < 40; i++) {
                if ("true".equals(js(web, "!!window.fixtureReady && typeof window.__finpulseExport === 'function'"))) { ready = true; break; }
                Thread.sleep(250);
            }
            check(ready, "fixture and export shim did not load");
            check("\"persisted\"".equals(js(web, "localStorage.getItem('smoke')")), "localStorage unavailable");
            js(web, "fetch('/session').then(r=>r.text()).then(t=>window.sessionResult=t)");
            for (int i = 0; i < 30 && !"\"authenticated\"".equals(js(web, "window.sessionResult")); i++) Thread.sleep(100);
            check("\"authenticated\"".equals(js(web, "window.sessionResult")), "same-origin cookie auth failed");
            runOnMainSync(web::reload);
            for (int i = 0; i < 30; i++) {
                if ("true".equals(js(web, "!!window.fixtureReady && typeof window.__finpulseExport === 'function'"))) break;
                Thread.sleep(200);
            }
            check("\"persisted\"".equals(js(web, "localStorage.getItem('smoke')")), "storage lost after reload");
            runOnMainSync(() -> {
                check(!web.getSettings().getAllowFileAccess(), "file access must stay disabled");
                check(web.getSettings().getJavaScriptEnabled(), "JavaScript disabled");
            });
            java.lang.reflect.Field pending = MainActivity.class.getDeclaredField("pendingExport"); pending.setAccessible(true);
            AtomicReference<byte[]> savedBytes = new AtomicReference<>();
            AtomicReference<String> savedName = new AtomicReference<>();
            AtomicInteger filePicks = new AtomicInteger(), externalLinks = new AtomicInteger();
            Activity finalActivityForMonitor = activity;
            ActivityMonitor monitor = new ActivityMonitor() {
                @Override public ActivityResult onStartActivity(Intent requested) {
                    if (Intent.ACTION_CREATE_DOCUMENT.equals(requested.getAction())) {
                        try { savedBytes.set((byte[]) pending.get(finalActivityForMonitor)); }
                        catch (Exception e) { throw new RuntimeException(e); }
                        savedName.set(requested.getStringExtra(Intent.EXTRA_TITLE));
                        return new ActivityResult(Activity.RESULT_CANCELED, null);
                    }
                    if (Intent.ACTION_OPEN_DOCUMENT.equals(requested.getAction())) {
                        filePicks.incrementAndGet(); return new ActivityResult(Activity.RESULT_CANCELED, null);
                    }
                    if (Intent.ACTION_VIEW.equals(requested.getAction())) {
                        externalLinks.incrementAndGet(); return new ActivityResult(Activity.RESULT_CANCELED, null);
                    }
                    return null;
                }
            };
            addMonitor(monitor);
            js(web, "(()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['{\"hello\":\"finpulse\"}'],{type:'application/json'}));a.download='finpulse-test.json';a.click();URL.revokeObjectURL(a.href)})()");
            for (int i = 0; i < 50 && savedBytes.get() == null; i++) Thread.sleep(100);
            check(savedBytes.get() != null, "Blob export did not open save picker");
            check("{\"hello\":\"finpulse\"}".equals(new String(savedBytes.get(), StandardCharsets.UTF_8)), "Blob bytes corrupted");
            check("finpulse-test.json".equals(savedName.get()), "Blob filename lost");
            savedBytes.set(null);
            js(web, "(()=>{const a=document.createElement('a');a.href='/export.csv';a.download='export.csv';document.body.append(a);a.click()})()");
            for (int i = 0; i < 50 && savedBytes.get() == null; i++) Thread.sleep(100);
            check(savedBytes.get() != null, "CSV download did not open save picker");
            check("code,name\n600000,FinPulse\n".equals(new String(savedBytes.get(), StandardCharsets.UTF_8)), "authenticated CSV download failed");
            AtomicInteger cancellations = new AtomicInteger();
            for (int i = 0; i < 2; i++) {
                runOnMainSync(() -> web.getWebChromeClient().onShowFileChooser(web, value -> { if (value == null) cancellations.incrementAndGet(); }, new WebChromeClient.FileChooserParams() {
                    public int getMode() { return MODE_OPEN; }
                    public boolean isCaptureEnabled() { return false; }
                    public CharSequence getTitle() { return null; }
                    public String getFilenameHint() { return null; }
                    public String[] getAcceptTypes() { return new String[]{"application/json"}; }
                    public Intent createIntent() { return new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("application/json"); }
                }));
                for (int j = 0; j < 20 && cancellations.get() <= i; j++) Thread.sleep(100);
            }
            check(filePicks.get() == 2 && cancellations.get() == 2, "file picker cancellation blocked retry");
            js(web, "(()=>{const a=document.createElement('a');a.href='https://example.com/news';document.body.append(a);a.click()})()");
            for (int i = 0; i < 20 && externalLinks.get() == 0; i++) Thread.sleep(100);
            check(externalLinks.get() == 1, "external link did not open browser");
            removeMonitor(monitor);
            result.putString("stream", "\nFINPULSE_SMOKE_PASS: launch, HTTP, JavaScript, cookie auth, localStorage, reload, JSON bytes/filename, authenticated CSV, file picker cancellation/retry, external browser, URL validation\n");
            finish(Activity.RESULT_OK, result);
        } catch (Throwable e) {
            result.putString("stream", "\nFINPULSE_SMOKE_FAIL: " + android.util.Log.getStackTraceString(e));
            finish(Activity.RESULT_CANCELED, result);
        } finally {
            if (activity != null) { Activity finalActivity = activity; runOnMainSync(finalActivity::finish); }
        }
    }
}
