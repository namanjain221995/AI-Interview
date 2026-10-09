import java.io.File;
import java.lang.reflect.Constructor;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * Run-mode launcher: finds the program's entry point among the compiled classes and invokes it
 * following the JDK 25 launch protocol (static or instance {@code main}, with or without
 * {@code String[]}), so classic programs, any public class name and compact source files all work.
 *
 * <p>Usage: {@code java ... SandboxLauncher <classesDir> <preferredClass>}
 */
public final class SandboxLauncher {
    private SandboxLauncher() {}

    public static void main(String[] argv) throws Exception {
        File dir = new File(argv[0]);
        String preferred = argv.length > 1 ? argv[1] : "Main";
        List<String> names = new ArrayList<>();
        String[] files = dir.list();
        if (files != null) {
            Arrays.sort(files);
            for (String f : files) {
                if (f.endsWith(".class") && f.indexOf('$') < 0) {
                    names.add(f.substring(0, f.length() - 6));
                }
            }
        }
        if (names.remove(preferred)) {
            names.add(0, preferred);
        }
        if (!preferred.equals("Main") && names.remove("Main")) {
            names.add(1, "Main");
        }
        ClassLoader loader = SandboxLauncher.class.getClassLoader();
        for (String name : names) {
            Class<?> cls;
            try {
                cls = Class.forName(name, false, loader);
            } catch (ClassNotFoundException | LinkageError e) {
                continue;
            }
            Method main = findMain(cls);
            if (main == null) {
                continue;
            }
            launch(cls, main);
            return;
        }
        System.err.println("error: no main method found; declare `public static void main(String[] args)` in class " + preferred);
        System.exit(1);
    }

    private static Method findMain(Class<?> cls) {
        Method[][] tiers = new Method[4][1];
        for (Method m : cls.getDeclaredMethods()) {
            if (!m.getName().equals("main") || m.getReturnType() != void.class || Modifier.isPrivate(m.getModifiers())) {
                continue;
            }
            boolean isStatic = Modifier.isStatic(m.getModifiers());
            Class<?>[] p = m.getParameterTypes();
            boolean withArgs = p.length == 1 && p[0] == String[].class;
            if (!withArgs && p.length != 0) {
                continue;
            }
            int tier = (isStatic ? 0 : 2) + (withArgs ? 0 : 1);
            tiers[tier][0] = m;
        }
        for (Method[] tier : tiers) {
            if (tier[0] != null) {
                return tier[0];
            }
        }
        return null;
    }

    private static void launch(Class<?> cls, Method main) {
        int status = 0;
        try {
            Class.forName(cls.getName(), true, cls.getClassLoader());
            main.setAccessible(true);
            Object target = null;
            if (!Modifier.isStatic(main.getModifiers())) {
                Constructor<?> ctor = cls.getDeclaredConstructor();
                ctor.setAccessible(true);
                target = ctor.newInstance();
            }
            if (main.getParameterCount() == 1) {
                main.invoke(target, (Object) new String[0]);
            } else {
                main.invoke(target);
            }
        } catch (InvocationTargetException e) {
            status = uncaught(e.getCause() != null ? e.getCause() : e);
        } catch (ExceptionInInitializerError e) {
            status = uncaught(e);
        } catch (ReflectiveOperationException | RuntimeException | LinkageError e) {
            status = uncaught(e);
        }
        System.out.flush();
        System.err.flush();
        if (status != 0) {
            System.exit(status);
        }
    }

    private static int uncaught(Throwable t) {
        System.out.flush();
        System.err.println("Exception in thread \"main\" " + SandboxHarness.describe(t));
        return 1;
    }
}
