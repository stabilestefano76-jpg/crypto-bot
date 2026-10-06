import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";
import { router } from "expo-router";

// Remembers the access code on this device (encrypted storage on the phone,
// browser storage on web) so it is typed only once. api.ts attaches it to
// every request; when the server refuses it, the user is sent to /login.

const STORAGE_KEY = "bot_access_key";

let cached: string | null = null;
let loading: Promise<void> | null = null;
let loginActive = false;
let redirecting = false;

async function readStored(): Promise<string | null> {
  try {
    if (Platform.OS === "web") return await AsyncStorage.getItem(STORAGE_KEY);
    return await SecureStore.getItemAsync(STORAGE_KEY);
  } catch {
    return null;
  }
}

async function writeStored(value: string | null): Promise<void> {
  try {
    if (Platform.OS === "web") {
      if (value === null) await AsyncStorage.removeItem(STORAGE_KEY);
      else await AsyncStorage.setItem(STORAGE_KEY, value);
    } else {
      if (value === null) await SecureStore.deleteItemAsync(STORAGE_KEY);
      else await SecureStore.setItemAsync(STORAGE_KEY, value);
    }
  } catch {
    // storage unavailable: the code simply won't be remembered
  }
}

// Waits for the first read from storage, so a request fired right at app
// start never goes out without a code that is actually saved.
export async function getAccessKey(): Promise<string | null> {
  if (!loading) {
    loading = readStored().then((v) => {
      cached = v;
    });
  }
  await loading;
  return cached;
}

export async function setAccessKey(value: string): Promise<void> {
  cached = value;
  loading = Promise.resolve();
  await writeStored(value);
}

export async function clearAccessKey(): Promise<void> {
  cached = null;
  loading = Promise.resolve();
  await writeStored(null);
}

// The login screen marks itself active so a stray refusal arriving while the
// user is already there doesn't bounce them back to the same screen.
export function setLoginActive(active: boolean): void {
  loginActive = active;
}

// Called when the server answers "access code missing/wrong". The stored code
// is dropped right away so the app stops sending a wrong one (repeated wrong
// codes would get this device temporarily blocked), then the login opens.
export async function handleUnauthorized(): Promise<void> {
  await clearAccessKey();
  if (loginActive || redirecting) return;
  redirecting = true;
  try {
    router.replace("/login");
  } catch {
    // navigation not ready yet: the next request will try again
  }
  setTimeout(() => {
    redirecting = false;
  }, 3000);
}
