package dev.observatory;

public class SyntheticMouseTest {
    public static void main(String[] args) {
        SyntheticMouse mouse = new SyntheticMouse();
        check(!mouse.cursorLocked(false, true));
        check(mouse.cursorLocked(true, false));
        mouse.button(1, true);
        check(!mouse.cursorLocked(false, true));
        mouse.button(0, true);
        check(mouse.cursorLocked(false, true));
        check(!mouse.cursorLocked(false, false));
        mouse.button(1, false);
        check(mouse.cursorLocked(false, true));
        mouse.button(0, false);
        check(!mouse.cursorLocked(false, true));
        mouse.button(0, true);
        mouse.release();
        check(!mouse.cursorLocked(false, true));
        System.out.println("PASS synthetic mining: unfocused held attack captures virtually; GUI, right click, release and physical capture preserved");
    }

    private static void check(boolean value) {
        if (!value) throw new AssertionError();
    }
}
