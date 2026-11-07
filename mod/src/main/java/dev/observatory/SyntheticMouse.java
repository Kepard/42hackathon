package dev.observatory;

public final class SyntheticMouse {
    private volatile boolean attack;

    public void button(int button, boolean pressed) {
        if (button == 0) attack = pressed;
    }

    public void release() { attack = false; }

    public boolean cursorLocked(boolean physical, boolean gameplay) {
        return physical || (attack && gameplay);
    }
}
