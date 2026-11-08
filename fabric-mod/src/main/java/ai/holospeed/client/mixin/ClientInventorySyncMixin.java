package ai.holospeed.client.mixin;

import ai.holospeed.client.ServerInventorySync;
import net.minecraft.client.network.ClientPlayNetworkHandler;
import net.minecraft.network.packet.s2c.play.InventoryS2CPacket;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(ClientPlayNetworkHandler.class)
public abstract class ClientInventorySyncMixin {
    @Inject(method = "onInventory", at = @At("TAIL"))
    private void holospeed$inventoryApplied(InventoryS2CPacket packet, CallbackInfo callback) {
        ServerInventorySync.INSTANCE.record(this, packet.getSyncId());
    }
}
