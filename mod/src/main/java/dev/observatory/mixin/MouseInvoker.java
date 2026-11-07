package dev.observatory.mixin;

import net.minecraft.client.Mouse;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Invoker;

@Mixin(Mouse.class)
public interface MouseInvoker {
    @Invoker("onMouseScroll") void observatoryScroll(long window, double horizontal, double vertical);
    @Invoker("onCursorPos") void observatoryCursor(long window, double x, double y);
}
