package dev.observatory;

public final class CommandPolicy {
    private CommandPolicy() {}
    public static boolean slashMessage(String message) { return message.stripLeading().startsWith("/"); }
}
