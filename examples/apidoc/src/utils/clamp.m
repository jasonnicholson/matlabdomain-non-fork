function y = clamp(x, lo, hi)
% CLAMP Limit X to the range [LO, HI] (a global function).
check_range(lo, hi);
y = min(max(x, lo), hi);
end
