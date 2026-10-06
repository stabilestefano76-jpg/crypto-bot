import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { router } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { colors, font, radius, spacing } from "@/src/theme";
import { setAccessKey, setLoginActive } from "@/src/auth";

const BASE = process.env.EXPO_PUBLIC_BACKEND_URL || "";

export default function LoginScreen() {
  const insets = useSafeAreaInsets();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoginActive(true);
    return () => setLoginActive(false);
  }, []);

  const submit = async () => {
    const value = code.trim();
    if (!value || busy) return;
    setBusy(true);
    setError(null);
    try {
      // Plain fetch on purpose (not the shared request helper): a wrong code
      // here must show a message, not trigger the automatic redirect.
      const res = await fetch(`${BASE}/api/auth/check`, {
        headers: { "X-Access-Key": value },
      });
      if (res.ok) {
        await setAccessKey(value);
        router.replace("/");
      } else if (res.status === 429) {
        setError("Troppi tentativi sbagliati. Riprova tra qualche minuto.");
      } else {
        setError("Codice errato.");
      }
    } catch {
      setError("Impossibile raggiungere il bot. Controlla la connessione.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={[styles.root, { paddingTop: insets.top }]}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <View style={styles.box}>
        <Ionicons name="lock-closed" size={36} color={colors.brand} />
        <Text style={styles.title}>Accesso</Text>
        <Text style={styles.subtitle}>
          Inserisci il codice di accesso. Lo scrivi una volta sola: questo
          dispositivo lo ricorderà.
        </Text>
        <TextInput
          style={styles.input}
          placeholder="Codice di accesso"
          placeholderTextColor={colors.onSurfaceSecondary}
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry
          value={code}
          onChangeText={setCode}
          onSubmitEditing={submit}
          returnKeyType="go"
          testID="input-access-code"
        />
        {error ? <Text style={styles.error}>{error}</Text> : null}
        <Pressable
          style={({ pressed }) => [styles.btn, pressed && { opacity: 0.7 }]}
          onPress={submit}
          disabled={busy}
          testID="button-access-submit"
        >
          {busy ? (
            <ActivityIndicator color={colors.onBrand} size="small" />
          ) : (
            <Text style={styles.btnText}>Entra</Text>
          )}
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface, justifyContent: "center" },
  box: { padding: spacing.xl, alignItems: "stretch", gap: spacing.sm },
  title: {
    color: colors.onSurface,
    fontSize: font.xl,
    fontWeight: "800",
    textAlign: "center",
    marginTop: spacing.sm,
  },
  subtitle: {
    color: colors.onSurfaceSecondary,
    fontSize: font.sm,
    textAlign: "center",
    marginBottom: spacing.md,
  },
  input: {
    backgroundColor: colors.surfaceTertiary,
    borderRadius: radius.md,
    padding: spacing.md,
    color: colors.onSurface,
    fontSize: font.lg,
  },
  error: { color: colors.error, fontSize: font.sm, textAlign: "center" },
  btn: {
    backgroundColor: colors.brand,
    borderRadius: radius.pill,
    paddingVertical: spacing.md,
    alignItems: "center",
    marginTop: spacing.sm,
  },
  btnText: { color: colors.onBrand, fontWeight: "800", fontSize: font.lg },
});
