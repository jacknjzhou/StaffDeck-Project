/** 判断当前测试环境是否提供可用的 localStorage（需同时实现 getItem 与 setItem）。 */
const hasUsableLocalStorage = typeof window !== 'undefined'
  && typeof window.localStorage?.getItem === 'function'
  && typeof window.localStorage?.setItem === 'function';

if (typeof window !== 'undefined' && !hasUsableLocalStorage) {
  const values = new Map<string, string>();
  const storage: Storage = {
    /** 返回当前已存储的键值对数量。 */
    get length() {
      return values.size;
    },
    /** 清空全部已存储的键值对。 */
    clear() {
      values.clear();
    },
    /** 读取指定键的值；键不存在时返回 null。 */
    getItem(key) {
      return values.get(key) ?? null;
    },
    /** 返回第 index 个键名；越界时返回 null。 */
    key(index) {
      return [...values.keys()][index] ?? null;
    },
    /** 删除指定键；键不存在时无副作用。 */
    removeItem(key) {
      values.delete(key);
    },
    /** 写入键值，值统一转换为字符串后保存。 */
    setItem(key, value) {
      values.set(key, String(value));
    },
  };
  Object.defineProperty(window, 'localStorage', { configurable: true, value: storage });
}
