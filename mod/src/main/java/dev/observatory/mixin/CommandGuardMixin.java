package dev.observatory.mixin;

import dev.observatory.CommandPolicy;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayNetworkHandler;
import net.minecraft.text.Text;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(ClientPlayNetworkHandler.class)
public class CommandGuardMixin {
    private static void blocked() {
        MinecraftClient.getInstance().inGameHud.getChatHud().addMessage(Text.literal("Commands are disabled by Observatory; normal chat is allowed."));
    }
    @Inject(method = "sendChatCommand", at = @At("HEAD"), cancellable = true)
    private void blockChatCommand(String command, CallbackInfo ci) { blocked(); ci.cancel(); }
    @Inject(method = "sendCommand", at = @At("HEAD"), cancellable = true)
    private void blockCommand(String command, CallbackInfoReturnable<Boolean> ci) { blocked(); ci.setReturnValue(false); }
    @Inject(method = "sendChatMessage", at = @At("HEAD"), cancellable = true)
    private void blockSlashMessage(String message, CallbackInfo ci) {
        if (CommandPolicy.slashMessage(message)) { blocked(); ci.cancel(); }
    }
}
