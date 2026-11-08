package ai.holospeed.client;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import net.fabricmc.loader.api.FabricLoader;

import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;

final class BridgeConfig {
    String url = "ws://127.0.0.1:8765/bridge";
    String token = "";
    int captureWidth = 960;

    static BridgeConfig load() throws IOException {
        Path path = FabricLoader.getInstance().getConfigDir().resolve("holospeed.json");
        Gson gson = new GsonBuilder().setPrettyPrinting().create();
        BridgeConfig config;
        if (Files.exists(path)) {
            config = gson.fromJson(Files.readString(path), BridgeConfig.class);
        } else {
            config = new BridgeConfig();
            Files.createDirectories(path.getParent());
            Files.writeString(path, gson.toJson(config));
        }
        if (config == null) throw new IOException("Invalid holospeed.json");
        String token = System.getenv("HOLOSPEED_BRIDGE_TOKEN");
        if (token != null && !token.isBlank()) config.token = token;
        URI uri = URI.create(config.url);
        if (!"ws".equals(uri.getScheme()) || !("127.0.0.1".equals(uri.getHost()) || "localhost".equals(uri.getHost()) || "[::1]".equals(uri.getHost()))) {
            throw new IOException("Bridge must use ws:// on loopback");
        }
        config.captureWidth = Math.max(320, Math.min(960, config.captureWidth));
        return config;
    }
}
