package dev.observatory;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;

public final class HandshakePolicy {
    private HandshakePolicy() {}

    public static boolean accepts(boolean hasOrigin, String origin, String authorization, String allowedOrigins, String token) {
        if (hasOrigin && (origin.isBlank() || Arrays.stream(allowedOrigins.split(",")).map(String::trim).noneMatch(origin::equals))) return false;
        return token == null || token.isEmpty() || MessageDigest.isEqual(
                ("Bearer " + token).getBytes(StandardCharsets.UTF_8), authorization.getBytes(StandardCharsets.UTF_8));
    }
}
