import java.util.*;
import java.util.function.*;
import java.util.stream.*;

/** AOT-cache training input: exercises the javac code paths typical submissions hit. */
public class Solution {
    record Edge(int to, long weight) {}

    enum Color { RED, GREEN, BLUE }

    sealed interface Shape permits Circle, Square {}
    record Circle(double r) implements Shape {}
    record Square(double side) implements Shape {}

    private final Map<String, List<Integer>> index = new HashMap<>();
    private static final int MOD = 1_000_000_007;

    public int[] twoSum(int[] nums, int target) {
        Map<Integer, Integer> seen = new HashMap<>();
        for (int i = 0; i < nums.length; i++) {
            Integer j = seen.get(target - nums[i]);
            if (j != null) {
                return new int[] {j, i};
            }
            seen.put(nums[i], i);
        }
        return new int[0];
    }

    public List<List<String>> groupAnagrams(String[] words) {
        return Arrays.stream(words)
                .collect(Collectors.groupingBy(w -> {
                    char[] c = w.toCharArray();
                    Arrays.sort(c);
                    return new String(c);
                }, TreeMap::new, Collectors.toList()))
                .values().stream().map(ArrayList::new).collect(Collectors.toList());
    }

    public long dijkstra(List<List<Edge>> graph, int src, int dst) {
        long[] dist = new long[graph.size()];
        Arrays.fill(dist, Long.MAX_VALUE);
        PriorityQueue<long[]> pq = new PriorityQueue<>(Comparator.comparingLong(a -> a[0]));
        dist[src] = 0;
        pq.offer(new long[] {0, src});
        while (!pq.isEmpty()) {
            long[] cur = pq.poll();
            int u = (int) cur[1];
            if (cur[0] > dist[u]) continue;
            for (Edge e : graph.get(u)) {
                long nd = dist[u] + e.weight();
                if (nd < dist[e.to()]) {
                    dist[e.to()] = nd;
                    pq.offer(new long[] {nd, e.to()});
                }
            }
        }
        return dist[dst] == Long.MAX_VALUE ? -1 : dist[dst];
    }

    public double area(Shape s) {
        return switch (s) {
            case Circle c -> Math.PI * c.r() * c.r();
            case Square q -> q.side() * q.side();
        };
    }

    public String describe(Color color, Object o) {
        String text = """
            color: %s
            value: %s
            """.formatted(color, o);
        if (o instanceof Integer n && n > 3) {
            text += "big";
        }
        var sb = new StringBuilder(text);
        Deque<Character> stack = new ArrayDeque<>();
        for (char ch : text.toCharArray()) {
            switch (ch) {
                case '(' -> stack.push(ch);
                case ')' -> { if (!stack.isEmpty()) stack.pop(); }
                default -> sb.append(Character.toUpperCase(ch));
            }
        }
        BiFunction<Integer, Integer, Integer> add = Integer::sum;
        Supplier<Set<String>> sets = TreeSet::new;
        Optional<String> first = index.keySet().stream().filter(k -> k.length() > add.apply(1, 2)).findFirst();
        int[][] grid = new int[3][4];
        IntStream.range(0, 3).forEach(i -> Arrays.fill(grid[i], i * MOD % 7));
        return sb + first.orElse("") + sets.get().size() + Arrays.deepToString(grid);
    }

    static <T extends Comparable<T>> T maxOf(Collection<? extends T> items) {
        Iterator<? extends T> it = items.iterator();
        T best = it.next();
        while (it.hasNext()) {
            T x = it.next();
            if (x.compareTo(best) > 0) best = x;
        }
        return best;
    }

    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        int n = in.hasNextInt() ? in.nextInt() : 0;
        System.out.println(Arrays.toString(new Solution().twoSum(new int[] {2, 7, 11, 15}, 9)) + n);
        System.out.println(maxOf(List.of(3, 9, 4)));
    }
}
