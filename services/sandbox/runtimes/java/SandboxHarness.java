import java.io.ByteArrayInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.lang.reflect.Array;
import java.lang.reflect.Constructor;
import java.lang.reflect.GenericArrayType;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.lang.reflect.ParameterizedType;
import java.lang.reflect.Type;
import java.lang.reflect.WildcardType;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Deque;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;
import java.util.Queue;
import java.util.Set;

/**
 * Function-call harness for Java submissions (precompiled into the runtime image).
 *
 * <p>Reads {@code {"class", "entry_candidates", "params", "return_type", "tests"}} from stdin, finds
 * the entry method on the candidate's class by reflection, converts JSON arguments to the method's
 * declared parameter types (arrays, boxed types and List/Set/Collection are all accepted), calls it
 * on a fresh instance per test and writes JSON records to fd 3. Expected values never reach this
 * process: the sandbox service compares the reported return values.
 */
public final class SandboxHarness {
    private static OutputStream results;

    private SandboxHarness() {}

    public static void main(String[] argv) throws IOException {
        results = new FileOutputStream("/proc/self/fd/3");
        String input = new String(System.in.readAllBytes(), StandardCharsets.UTF_8);
        System.setIn(new ByteArrayInputStream(new byte[0]));
        int code = run(input);
        System.out.flush();
        System.err.flush();
        results.flush();
        Runtime.getRuntime().halt(code);
    }

    @SuppressWarnings("unchecked")
    private static int run(String input) {
        Map<String, Object> payload = (Map<String, Object>) SandboxJson.parse(input);
        String className = String.valueOf(payload.getOrDefault("class", "Solution"));
        List<String> candidates = new ArrayList<>();
        for (Object c : (List<Object>) payload.getOrDefault("entry_candidates", List.of())) {
            candidates.add(String.valueOf(c));
        }
        List<Object> params = (List<Object>) payload.getOrDefault("params", List.of());
        List<String> sigTypes = new ArrayList<>();
        for (Object p : params) {
            sigTypes.add(String.valueOf(((Map<String, Object>) p).get("type")));
        }
        String wanted = candidates.isEmpty() ? "?" : candidates.get(0);

        Class<?> cls;
        try {
            cls = Class.forName(className, true, SandboxHarness.class.getClassLoader());
        } catch (ClassNotFoundException e) {
            emitFatal("entry", "class " + className + " is not defined (declare: class " + className + " { ... })");
            return 1;
        } catch (ExceptionInInitializerError e) {
            emitFatal("runtime", describe(e.getCause() != null ? e.getCause() : e));
            return 1;
        } catch (LinkageError e) {
            emitFatal("runtime", describe(e));
            return 1;
        }
        Method method = findMethod(cls, candidates, sigTypes);
        if (method == null) {
            emitFatal("entry", "method " + wanted + " with " + sigTypes.size() + " parameter(s) is not defined in class " + className);
            return 1;
        }
        method.setAccessible(true);
        boolean isStatic = Modifier.isStatic(method.getModifiers());
        Constructor<?> ctor = null;
        if (!isStatic) {
            try {
                ctor = cls.getDeclaredConstructor();
                ctor.setAccessible(true);
            } catch (NoSuchMethodException e) {
                emitFatal("entry", "class " + className + " needs a no-argument constructor");
                return 1;
            }
        }
        Type[] paramTypes = method.getGenericParameterTypes();
        emit("{\"t\":\"ready\"}");

        for (Object rawTest : (List<Object>) payload.getOrDefault("tests", List.of())) {
            Map<String, Object> test = (Map<String, Object>) rawTest;
            long index = ((Number) test.get("i")).longValue();
            emit("{\"t\":\"begin\",\"i\":" + index + "}");
            List<Object> args = (List<Object>) test.getOrDefault("args", List.of());
            Object[] converted = new Object[paramTypes.length];
            try {
                if (args.size() != paramTypes.length) {
                    throw new IllegalArgumentException("expected " + paramTypes.length + " arguments, got " + args.size());
                }
                for (int k = 0; k < paramTypes.length; k++) {
                    converted[k] = convert(args.get(k), paramTypes[k]);
                }
            } catch (RuntimeException e) {
                emitError(index, "harness", "bad arguments: " + e.getMessage(), -1);
                continue;
            }
            long started = System.nanoTime();
            try {
                Object target = isStatic ? null : ctor.newInstance();
                Object value = method.invoke(target, converted);
                double ms = (System.nanoTime() - started) / 1e6;
                String actual;
                try {
                    actual = SandboxJson.write(value);
                } catch (RuntimeException e) {
                    emitError(index, "runtime", "invalid return value: " + e.getMessage(), ms);
                    continue;
                }
                emit("{\"t\":\"result\",\"i\":" + index + ",\"ok\":true,\"actual\":" + actual + ",\"ms\":" + ms + "}");
            } catch (InvocationTargetException e) {
                double ms = (System.nanoTime() - started) / 1e6;
                Throwable cause = e.getCause() != null ? e.getCause() : e;
                emitError(index, cause instanceof OutOfMemoryError ? "memory" : "runtime", describe(cause), ms);
            } catch (OutOfMemoryError e) {
                emitError(index, "memory", describe(e), (System.nanoTime() - started) / 1e6);
            } catch (ReflectiveOperationException | RuntimeException e) {
                emitError(index, "runtime", describe(e), (System.nanoTime() - started) / 1e6);
            } catch (StackOverflowError e) {
                emitError(index, "runtime", describe(e), (System.nanoTime() - started) / 1e6);
            }
        }
        System.out.flush();
        emit("{\"t\":\"done\"}");
        return 0;
    }

    // ------------------------------------------------------------- reflection

    private static Method findMethod(Class<?> cls, List<String> names, List<String> sigTypes) {
        List<Method> all = new ArrayList<>();
        for (Class<?> c = cls; c != null && c != Object.class; c = c.getSuperclass()) {
            for (Method m : c.getDeclaredMethods()) {
                all.add(m);
            }
        }
        for (String name : names) {
            Method best = null;
            int bestScore = -1;
            for (Method m : all) {
                if (!m.getName().equals(name) || m.getParameterCount() != sigTypes.size() || m.isSynthetic() || m.isBridge()) {
                    continue;
                }
                int score = 0;
                Class<?>[] types = m.getParameterTypes();
                for (int k = 0; k < types.length; k++) {
                    if (types[k] == preferredJavaType(sigTypes.get(k))) {
                        score++;
                    }
                }
                if (score > bestScore) {
                    best = m;
                    bestScore = score;
                }
            }
            if (best != null) {
                return best;
            }
        }
        return null;
    }

    private static Class<?> preferredJavaType(String sigType) {
        switch (sigType) {
            case "int": return int.class;
            case "long": return long.class;
            case "float": return double.class;
            case "bool": return boolean.class;
            case "string": return String.class;
            case "int[]": return int[].class;
            case "long[]": return long[].class;
            case "float[]": return double[].class;
            case "bool[]": return boolean[].class;
            case "string[]": return String[].class;
            case "int[][]": return int[][].class;
            case "string[][]": return String[][].class;
            default: return Object.class;
        }
    }

    @SuppressWarnings({"unchecked", "rawtypes"})
    static Object convert(Object json, Type type) {
        if (type instanceof WildcardType) {
            Type[] upper = ((WildcardType) type).getUpperBounds();
            return convert(json, upper.length > 0 ? upper[0] : Object.class);
        }
        if (type instanceof ParameterizedType) {
            ParameterizedType pt = (ParameterizedType) type;
            Class<?> raw = (Class<?>) pt.getRawType();
            Type[] typeArgs = pt.getActualTypeArguments();
            if (Map.class.isAssignableFrom(raw)) {
                Map<Object, Object> out = new LinkedHashMap<>();
                for (Map.Entry<String, Object> e : ((Map<String, Object>) asMap(json)).entrySet()) {
                    out.put(convert(e.getKey(), typeArgs[0]), convert(e.getValue(), typeArgs[1]));
                }
                return out;
            }
            if (Collection.class.isAssignableFrom(raw) || raw == Iterable.class) {
                Collection out = newCollection(raw);
                for (Object item : asList(json)) {
                    out.add(convert(item, typeArgs[0]));
                }
                return out;
            }
            return convert(json, raw);
        }
        if (type instanceof GenericArrayType) {
            Type component = ((GenericArrayType) type).getGenericComponentType();
            List<Object> list = asList(json);
            Class<?> rawComponent = rawClass(component);
            Object arr = Array.newInstance(rawComponent, list.size());
            for (int i = 0; i < list.size(); i++) {
                Array.set(arr, i, convert(list.get(i), component));
            }
            return arr;
        }
        Class<?> c = (Class<?>) type;
        if (json == null) {
            if (c.isPrimitive()) {
                throw new IllegalArgumentException("null cannot be passed as " + c.getName());
            }
            return null;
        }
        if (c == int.class || c == Integer.class) {
            return Math.toIntExact(asLong(json));
        }
        if (c == long.class || c == Long.class) {
            return asLong(json);
        }
        if (c == double.class || c == Double.class) {
            return asNumber(json).doubleValue();
        }
        if (c == float.class || c == Float.class) {
            return asNumber(json).floatValue();
        }
        if (c == short.class || c == Short.class) {
            return (short) Math.toIntExact(asLong(json));
        }
        if (c == byte.class || c == Byte.class) {
            return (byte) Math.toIntExact(asLong(json));
        }
        if (c == boolean.class || c == Boolean.class) {
            if (json instanceof Boolean) {
                return json;
            }
            throw new IllegalArgumentException("expected a boolean, got " + json);
        }
        if (c == char.class || c == Character.class) {
            String s = String.valueOf(json);
            if (s.length() != 1) {
                throw new IllegalArgumentException("expected a single character, got " + s);
            }
            return s.charAt(0);
        }
        if (c == String.class || c == CharSequence.class) {
            if (json instanceof String) {
                return json;
            }
            throw new IllegalArgumentException("expected a string, got " + json);
        }
        if (c.isArray()) {
            List<Object> list = asList(json);
            Class<?> component = c.getComponentType();
            Object arr = Array.newInstance(component, list.size());
            for (int i = 0; i < list.size(); i++) {
                Array.set(arr, i, convert(list.get(i), component));
            }
            return arr;
        }
        if (Collection.class.isAssignableFrom(c) || c == Iterable.class) {
            Collection out = newCollection(c);
            for (Object item : asList(json)) {
                out.add(normalize(item));
            }
            return out;
        }
        if (c == Object.class || c == Number.class) {
            return normalize(json);
        }
        throw new IllegalArgumentException("unsupported parameter type " + c.getName());
    }

    private static Object normalize(Object json) {
        if (json instanceof Long) {
            long v = (Long) json;
            return v >= Integer.MIN_VALUE && v <= Integer.MAX_VALUE ? (Object) (int) v : json;
        }
        return json;
    }

    private static Class<?> rawClass(Type type) {
        if (type instanceof Class) {
            return (Class<?>) type;
        }
        if (type instanceof ParameterizedType) {
            return (Class<?>) ((ParameterizedType) type).getRawType();
        }
        if (type instanceof GenericArrayType) {
            return Array.newInstance(rawClass(((GenericArrayType) type).getGenericComponentType()), 0).getClass();
        }
        return Object.class;
    }

    @SuppressWarnings({"rawtypes", "unchecked"})
    private static Collection newCollection(Class<?> raw) {
        if (!raw.isInterface() && !Modifier.isAbstract(raw.getModifiers())) {
            try {
                return (Collection) raw.getDeclaredConstructor().newInstance();
            } catch (ReflectiveOperationException e) {
                throw new IllegalArgumentException("cannot instantiate " + raw.getName());
            }
        }
        if (Set.class.isAssignableFrom(raw)) {
            return Set.class == raw ? new HashSet<>() : new LinkedHashSet<>();
        }
        if (Deque.class.isAssignableFrom(raw)) {
            return new ArrayDeque<>();
        }
        if (Queue.class.isAssignableFrom(raw)) {
            return new LinkedList<>();
        }
        return new ArrayList<>();
    }

    @SuppressWarnings("unchecked")
    private static List<Object> asList(Object json) {
        if (json instanceof List) {
            return (List<Object>) json;
        }
        throw new IllegalArgumentException("expected an array, got " + json);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> asMap(Object json) {
        if (json instanceof Map) {
            return (Map<String, Object>) json;
        }
        throw new IllegalArgumentException("expected an object, got " + json);
    }

    private static Number asNumber(Object json) {
        if (json instanceof Number) {
            return (Number) json;
        }
        throw new IllegalArgumentException("expected a number, got " + json);
    }

    private static long asLong(Object json) {
        if (json instanceof Long) {
            return (Long) json;
        }
        if (json instanceof BigInteger) {
            return ((BigInteger) json).longValueExact();
        }
        if (json instanceof Double) {
            double d = (Double) json;
            if (d == Math.rint(d) && !Double.isInfinite(d)) {
                return (long) d;
            }
        }
        throw new IllegalArgumentException("expected an integer, got " + json);
    }

    // ------------------------------------------------------------- reporting

    static String describe(Throwable t) {
        StringBuilder sb = new StringBuilder();
        appendThrowable(sb, t, 0);
        return sb.toString();
    }

    private static void appendThrowable(StringBuilder sb, Throwable t, int depth) {
        sb.append(t.toString());
        int shown = 0;
        for (StackTraceElement el : t.getStackTrace()) {
            String cn = el.getClassName();
            if (cn.startsWith("SandboxHarness") || cn.startsWith("SandboxJson") || cn.startsWith("SandboxLauncher")
                    || cn.startsWith("java.lang.reflect.") || cn.startsWith("jdk.internal.reflect.")
                    || cn.startsWith("sun.reflect.")) {
                continue;
            }
            if (shown++ >= 16) {
                sb.append("\n\t...");
                break;
            }
            sb.append("\n\tat ").append(el);
        }
        Throwable cause = t.getCause();
        if (cause != null && cause != t && depth < 3) {
            sb.append("\nCaused by: ");
            appendThrowable(sb, cause, depth + 1);
        }
    }

    private static void emitFatal(String kind, String message) {
        StringBuilder sb = new StringBuilder("{\"t\":\"fatal\",\"kind\":");
        SandboxJson.writeString(sb, kind);
        sb.append(",\"message\":");
        SandboxJson.writeString(sb, message);
        emit(sb.append('}').toString());
    }

    private static void emitError(long index, String type, String message, double ms) {
        StringBuilder sb = new StringBuilder("{\"t\":\"result\",\"i\":").append(index).append(",\"ok\":false,\"error_type\":");
        SandboxJson.writeString(sb, type);
        sb.append(",\"error\":");
        SandboxJson.writeString(sb, message);
        if (ms >= 0) {
            sb.append(",\"ms\":").append(ms);
        }
        emit(sb.append('}').toString());
    }

    private static void emit(String line) {
        try {
            results.write((line + "\n").getBytes(StandardCharsets.UTF_8));
            results.flush();
        } catch (IOException e) {
            Runtime.getRuntime().halt(3);
        }
    }
}
