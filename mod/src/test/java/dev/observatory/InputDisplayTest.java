package dev.observatory;

public final class InputDisplayTest {
    private static void check(boolean value) { if (!value) throw new AssertionError(); }
    public static void main(String[] args) {
        InputDisplay display = new InputDisplay();
        display.key(87, true, 0); display.key(340, true, 0); display.mouse(0,true,0);
        check(display.lines(1_000_000_000L).get(0).text().contains("Shift+LMB 1.0s"));
        display.mouse(0,false,3_200_000_000L);
        check(display.lines(3_200_000_000L).stream().anyMatch(line -> line.text().equals("Shift+LMB hold 3.2s")));
        int alpha = display.lines(4_000_000_000L).get(3).alpha();
        check(display.lines(5_000_000_000L).get(3).alpha() < alpha);
        display.clear(5_000_000_000L);
        check(display.lines(5_000_000_000L).get(0).text().equals("Held: —"));
        check(!display.active(12_000_000_000L));
        System.out.println("PASS input display: held keys/buttons, Shift+click chord,3.2s hold, fading history and release cleanup");
    }
}
