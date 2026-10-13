// A minimal valid solution: the smallest graph size, every graph empty, and every
// query answered with graph 0. It is right only when the hidden graph happens to be 0.
#include <bits/stdc++.h>
using namespace std;

int main() {
    int M;
    double eps;
    cin >> M >> eps;
    const int N = 4;
    cout << N << "\n";
    for (int k = 0; k < M; k++) cout << string(N * (N - 1) / 2, '0') << "\n";
    cout.flush();
    for (int q = 0; q < 100; q++) {
        string h;
        cin >> h;
        cout << 0 << endl;
    }
}
