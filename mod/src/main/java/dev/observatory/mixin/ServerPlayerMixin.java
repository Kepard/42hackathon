package dev.observatory.mixin;

import dev.observatory.Observatory;
import net.minecraft.server.network.ServerPlayerEntity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(ServerPlayerEntity.class)
public class ServerPlayerMixin {
    @Inject(method = {"tick", "playerTick"}, at = @At("HEAD"), cancellable = true)
    private void freezePlayer(CallbackInfo ci) {
        if (Observatory.isPaused()) ci.cancel();
    }
}
