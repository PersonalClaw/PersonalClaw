/**
 * Sorts instance paths numerically on their indices to match the backend's `_natural_key`.
 * Ensures `root.children[10]` sorts after `root.children[2]`.
 */
export const compareInstancePaths = (pathA: string = "", pathB: string = ""): number => {
    return pathA.localeCompare(pathB, undefined, { 
        numeric: true, 
        sensitivity: 'base' 
    });
};