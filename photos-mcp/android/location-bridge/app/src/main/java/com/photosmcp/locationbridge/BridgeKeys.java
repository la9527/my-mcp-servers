package com.photosmcp.locationbridge;

import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.PublicKey;
import java.security.Signature;
import java.security.spec.ECGenParameterSpec;

import javax.crypto.KeyGenerator;
import javax.crypto.Mac;
import javax.crypto.SecretKey;

final class BridgeKeys {
    private static final String STORE = "AndroidKeyStore";
    private static final String SIGNING_ALIAS = "photosmcp-location-signing-v1";
    private static final String OWNER_ALIAS = "photosmcp-owner-signing-v1";
    private static final String OUTBOX_ALIAS = "photosmcp-location-outbox-v1";
    private static final String ASSET_ALIAS = "photosmcp-location-asset-hmac-v1";

    private BridgeKeys() {}

    static KeyPair signingKey() throws Exception {
        return keyPair(SIGNING_ALIAS);
    }

    static KeyPair ownerKey() throws Exception {
        return keyPair(OWNER_ALIAS);
    }

    private static KeyPair keyPair(String alias) throws Exception {
        KeyStore keyStore = KeyStore.getInstance(STORE);
        keyStore.load(null);
        if (!keyStore.containsAlias(alias)) {
            KeyPairGenerator generator = KeyPairGenerator.getInstance(
                    KeyProperties.KEY_ALGORITHM_EC, STORE);
            generator.initialize(new KeyGenParameterSpec.Builder(
                    alias, KeyProperties.PURPOSE_SIGN | KeyProperties.PURPOSE_VERIFY)
                    .setAlgorithmParameterSpec(new ECGenParameterSpec("secp256r1"))
                    .setDigests(KeyProperties.DIGEST_SHA256)
                    .build());
            generator.generateKeyPair();
        }
        PrivateKey privateKey = (PrivateKey) keyStore.getKey(alias, null);
        PublicKey publicKey = keyStore.getCertificate(alias).getPublicKey();
        return new KeyPair(publicKey, privateKey);
    }

    static SecretKey outboxKey() throws Exception {
        return aesKey(OUTBOX_ALIAS);
    }

    static String assetKey(String stableInput) throws Exception {
        KeyStore keyStore = KeyStore.getInstance(STORE);
        keyStore.load(null);
        if (!keyStore.containsAlias(ASSET_ALIAS)) {
            KeyGenerator generator = KeyGenerator.getInstance(
                    KeyProperties.KEY_ALGORITHM_HMAC_SHA256, STORE);
            generator.init(new KeyGenParameterSpec.Builder(
                    ASSET_ALIAS, KeyProperties.PURPOSE_SIGN)
                    .setDigests(KeyProperties.DIGEST_SHA256)
                    .build());
            generator.generateKey();
        }
        SecretKey key = (SecretKey) keyStore.getKey(ASSET_ALIAS, null);
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(key);
        return hex(mac.doFinal(stableInput.getBytes(StandardCharsets.UTF_8)));
    }

    static String publicKeyPem() throws Exception {
        return publicKeyPem(signingKey().getPublic());
    }

    static String ownerPublicKeyPem() throws Exception {
        return publicKeyPem(ownerKey().getPublic());
    }

    private static String publicKeyPem(PublicKey publicKey) {
        String encoded = Base64.encodeToString(publicKey.getEncoded(), Base64.NO_WRAP);
        StringBuilder wrapped = new StringBuilder();
        for (int offset = 0; offset < encoded.length(); offset += 64) {
            wrapped.append(encoded, offset, Math.min(offset + 64, encoded.length())).append('\n');
        }
        return "-----BEGIN PUBLIC KEY-----\n" + wrapped + "-----END PUBLIC KEY-----\n";
    }

    static String sign(byte[] message) throws Exception {
        return signWith(signingKey().getPrivate(), message);
    }

    static String signOwner(byte[] message) throws Exception {
        return signWith(ownerKey().getPrivate(), message);
    }

    private static String signWith(PrivateKey privateKey, byte[] message) throws Exception {
        Signature signature = Signature.getInstance("SHA256withECDSA");
        signature.initSign(privateKey);
        signature.update(message);
        return Base64.encodeToString(signature.sign(), Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING);
    }

    private static SecretKey aesKey(String alias) throws Exception {
        KeyStore keyStore = KeyStore.getInstance(STORE);
        keyStore.load(null);
        if (!keyStore.containsAlias(alias)) {
            KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, STORE);
            generator.init(new KeyGenParameterSpec.Builder(
                    alias, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build());
            generator.generateKey();
        }
        return (SecretKey) keyStore.getKey(alias, null);
    }

    static String hex(byte[] value) {
        StringBuilder result = new StringBuilder(value.length * 2);
        for (byte item : value) result.append(String.format("%02x", item & 0xff));
        return result.toString();
    }
}
