import { compareInstancePaths } from './sort';
import { describe, it, expect } from 'vitest'

describe('compareInstancePaths', () => {
    it('sorts fan-out paths naturally rather than lexically', () => {
        const input = [
            'root.children[10]',
            'root.children[1]',
            'root.children[11]',
            'root.children[3]',
            'root.children[2]',
        ];
        
        const expected = [
            'root.children[1]',
            'root.children[2]',
            'root.children[3]',
            'root.children[10]',
            'root.children[11]',
        ];

        expect([...input].sort(compareInstancePaths)).toEqual(expected);
    });

    it('sorts loop iteration paths naturally', () => {
        const input = [
            'root.body@10',
            'root.body@2',
            'root.body@11',
            'root.body@1',
        ];
        
        const expected = [
            'root.body@1',
            'root.body@2',
            'root.body@10',
            'root.body@11',
        ];

        expect([...input].sort(compareInstancePaths)).toEqual(expected);
    });
});