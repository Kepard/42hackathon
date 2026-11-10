package dev.observatory;

public class SyntheticKeysTest {
    public static void main(String[] args) {
        SyntheticKeys keys = new SyntheticKeys();
        keys.set(340,true); keys.set(344,true); keys.set(340,false);
        check(keys.modifiers()==1 && keys.held(344));
        keys.set(341,true); keys.set(342,true); keys.set(343,true);
        check(keys.modifiers()==15);
        keys.set(292,true); keys.set(71,true); check(keys.held(292)&&keys.held(71));
        keys.set(292,false); check(!keys.held(292)&&keys.held(71));
        keys.clear(); check(keys.modifiers()==0&&!keys.held(71));
        for (String command : new String[]{"/give @s diamond", "  /tp 0 80 0", "/gamemode creative", "/"}) check(CommandPolicy.slashMessage(command));
        for (String chat : new String[]{"", "hello", "let's go / north", "give me a log"}) check(!CommandPolicy.slashMessage(chat));
        System.out.println("PASS synthetic keyboard:independent left/right modifiers, combined mask, F3 chord hold/release and cleanup");
    }
    private static void check(boolean value) { if (!value) throw new AssertionError(); }
}
