package ai.holospeed.client;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

final class BridgeTransport implements WebSocket.Listener {
    private final BridgeConfig config;
    private final Consumer<JsonObject> receiver;
    private final Runnable disconnected;
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
    private final AtomicBoolean connecting = new AtomicBoolean();
    private final StringBuilder message = new StringBuilder();
    private volatile WebSocket socket;
    private CompletableFuture<Void> sendTail = CompletableFuture.completedFuture(null);

    BridgeTransport(BridgeConfig config, Consumer<JsonObject> receiver, Runnable disconnected) {
        this.config = config;
        this.receiver = receiver;
        this.disconnected = disconnected;
    }

    boolean connected() { return socket != null; }

    void connect() {
        if (config.token.isBlank() || connected() || !connecting.compareAndSet(false, true)) return;
        http.newWebSocketBuilder().connectTimeout(Duration.ofSeconds(3))
            .header("Authorization", "Bearer " + config.token)
            .buildAsync(URI.create(config.url), this)
            .whenComplete((ws, error) -> {
                connecting.set(false);
                if (error != null) disconnected.run();
            });
    }

    @Override public void onOpen(WebSocket webSocket) {
        socket = webSocket;
        webSocket.request(1);
        JsonObject hello = new JsonObject();
        hello.addProperty("type", "hello");
        hello.addProperty("protocol", 1);
        hello.addProperty("minecraft", "1.21.1");
        send(hello);
    }

    synchronized void send(JsonObject value) {
        WebSocket ws = socket;
        if (ws == null) return;
        sendTail = sendTail.handle((ignored, error) -> null)
            .thenCompose(ignored -> ws.sendText(value.toString(), true))
            .handle((ignored, error) -> { if (error != null) fail(ws); return null; });
    }

    @Override public CompletionStage<?> onText(WebSocket ws, CharSequence data, boolean last) {
        if (message.length() + data.length() > 65536) { fail(ws); return null; }
        message.append(data);
        if (last) {
            try { receiver.accept(JsonParser.parseString(message.toString()).getAsJsonObject()); }
            catch (RuntimeException error) { fail(ws); }
            message.setLength(0);
        }
        ws.request(1);
        return null;
    }

    @Override public CompletionStage<?> onClose(WebSocket ws, int code, String reason) { fail(ws); return null; }
    @Override public void onError(WebSocket ws, Throwable error) { fail(ws); }

    private void fail(WebSocket ws) {
        if (socket == ws) { socket = null; ws.abort(); disconnected.run(); }
    }

    void close() {
        WebSocket ws = socket;
        socket = null;
        if (ws != null) ws.abort();
    }
}
