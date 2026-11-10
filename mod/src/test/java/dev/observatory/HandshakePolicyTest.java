package dev.observatory;

public class HandshakePolicyTest {
    public static void main(String[] args) {
        check(HandshakePolicy.accepts(false, "", "", "", null));
        check(!HandshakePolicy.accepts(true, "https://evil.test", "", "", null));
        check(!HandshakePolicy.accepts(true, "", "", "", null));
        check(HandshakePolicy.accepts(true, "https://dashboard.test", "", "https://other.test, https://dashboard.test", null));
        check(!HandshakePolicy.accepts(false, "", "", "", "secret"));
        check(!HandshakePolicy.accepts(false, "", "Bearer wrong", "", "secret"));
        check(HandshakePolicy.accepts(false, "", "Bearer secret", "", "secret"));
        check(!HandshakePolicy.accepts(true, "https://evil.test", "Bearer secret", "", "secret"));
        System.out.println("PASS handshake policy: native, browser rejection, allowlist and token");
    }

    private static void check(boolean value) {
        if (!value) throw new AssertionError();
    }
}
