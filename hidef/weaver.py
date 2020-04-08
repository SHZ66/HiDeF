import numpy as np
import scipy as sp
import networkx as nx
from collections import Counter, defaultdict
from itertools import product as iproduct
from sys import getrecursionlimit, setrecursionlimit

from hidef import LOGGER

__all__ = ['Weaver', 'weave']

istuple = lambda n: isinstance(n, tuple)
isdummy = lambda n: None in n
internals = lambda T: (node for node in T if istuple(node))
RECURSION_MAX_DEPTH = int(10e6)

class Weaver(object):
    """
    Class for constructing a hierarchical representation of a graph 
    based on (a list of) input partitions. 

    Examples
    --------
    Define a list of partitions P:

    >>> P = ['11111111',
    ...      '11111100',
    ...      '00001111',
    ...      '11100000',
    ...      '00110000',
    ...      '00001100',
    ...      '00000011']

    Define terminal node labels:

    >>> nodes = 'ABCDEFGH'

    Construct a hierarchy based on P:

    >>> weaver = Weaver()
    >>> weaver.weave(P, terminals=nodes, levels=False, top=10, cutoff=0.9)

    """

    __slots__ = ['_assignment', '_terminals', 'assume_levels', 'hier', '_levels', '_labels',
                 '_full', '_secondary_edges', '_secondary_terminal_edges']

    def __init__(self):
        self.hier = None
        self._full = None
        self._secondary_edges = None
        self._secondary_terminal_edges = None
        self._labels = None
        self._levels = None
        self.assume_levels = False
        self._terminals = None
        self._assignment = None

    def number_of_terminals(self):
        if self._assignment is None:
            return 0
        return len(self._assignment[0])

    n_terminals = property(number_of_terminals, 'the number of terminal nodes')    

    def set_terminals(self, value):
        if value is None:
            terminals = np.arange(self.n_terminals)
        elif not isinstance(value, np.ndarray):
            terminals = [v for v in value]    # this is to convert list of strings to chararray
        
        if len(terminals) != self.n_terminals:
            raise ValueError('terminal nodes size mismatch: %d instead of %d'
                                %(len(terminals), self.n_terminals))

        self._terminals = np.asarray(terminals)

    def get_terminals(self):
        return self._terminals

    terminals = property(get_terminals, set_terminals, 
                          doc='terminals nodes')

    def get_assignment(self):
        return self._assignment

    assignment = property(get_assignment, doc='assignment matrix')

    def relabel(self):
        """Changes the labels of the nodes to the depths."""

        mapping = {}
        map_indices = defaultdict(int)
        if self.assume_levels:
            map_dict = self.level()
        else:
            map_dict = self.depth()
            
        for node in map_dict:
            if istuple(node):
                value = map_dict[node]
                idx = map_indices[value]
                mapping[node] = (value, idx)

                map_indices[value] += 1

        self.hier = nx.relabel_nodes(self.hier, mapping, copy=True)
        return mapping

    def get_levels(self):
        """Returns the levels (ordered ascendingly) in the hierarchy."""

        if self.hier is None:
            raise ValueError('hierarchy not built. Call weave() first')

        T = self.hier
        levels = []

        for node in internals(T): 
            level = T.nodes[node]['level']
            if level not in levels:
                levels.append(level)

        levels.sort()
        return levels

    def some_node(self, level):
        """Returns the first node that is associated with the partition specified by level."""

        if self.hier is None:
            raise ValueError('hierarchy not built. Call weave() first')

        T = self.hier

        for node in internals(T):
            if T.nodes[node]['level'] == level:
                return node

    def weave(self, partitions, terminals=None, boolean=False, levels=False, **kwargs):
        """Finds a directed acyclic graph that represents a hierarchy recovered from 
        partitions.

        Parameters
        ----------
        partitions : positional argument 
            a list of different partitions of the graph. Each item in the 
            list should be an array (Numpy array or list) of partition labels 
            for the nodes. A root partition (where all nodes belong to one 
            cluster) and a terminal partition (where all nodes belong to 
            their own cluster) will automatically added later.

        terminals : keyword argument, optional (default=None)
            a list of names for the graph nodes. If none is provided, an 
            integer will be assigned to each node.

        levels : keyword argument, optional (default=False)
            whether assume the partitions are provided in some order. If set 
            to True, the algorithm will only find the parents for a node from 
            upper levels. The levels are assumed to be arranged in an ascending 
            order, e.g. partitions[0] is the highest level and partitions[-1] 
            the lowest.

        boolean : keyword argument, optional (default=False)
            whether the partition labels should be considered as boolean. If 
            set to True, only the clusters labelled as True will be considered 
            as a parent in the hierarchy.

        top : keyword argument (0 ~ 100, default=100)
            top x percent (alternative) edges to be kept in the hierarchy. This parameter 
            controls the number of parents each node has based on a global ranking of the 
            edges. Note that if top=0 then each node will only have exactly one parent 
            (except for the root which has none). 

        cutoff : keyword argument (0.5 ~ 1.0, default=0.8)
            containment index cutoff for claiming parenthood. c

        See Also
        --------
        build
        pick

        Returns
        -------
        T : networkx.DiGraph
            
        """

        top = kwargs.pop('top', 100)

        ## checkers
        n_sets = len(partitions)
        if n_sets == 0:
            raise ValueError('partitions cannot be empty')
        
        lengths = set([len(l) for l in partitions])
        if len(lengths) > 1:
            raise ValueError('partitions must have the same length')
        n_nodes = lengths.pop()

        if not isinstance(partitions, np.ndarray):
            arr = [[None]*n_nodes for _ in range(n_sets)] # ndarray(object) won't treat '1's correctly
            for i in range(n_sets):
                for j in range(n_nodes):
                    arr[i][j] = partitions[i][j]  # this is to deal with list-of-strings case
        else:
            arr = partitions
        partitions = arr 

        ## initialization
        if isinstance(levels, bool):
            self.assume_levels = levels
            levels = None
        else:
            self.assume_levels = True
        clevels = levels if levels is not None else np.arange(n_sets)
        if len(clevels) != n_sets:
            raise ValueError('levels/partitions length mismatch: %d/%d'%(len(levels), n_sets))

        if boolean:
            # convert partitions twice for CI calculation
            self._assignment = np.asarray(partitions).astype(bool)  # asarray(partitions, dtype=bool) won't treat '1's correctly
            self._labels = np.ones(n_sets, dtype=bool)
            self._levels = clevels
        else:
            L = []; indices = []; labels = []; levels = []
            for i, p in enumerate(partitions):
                p = np.asarray(p)
                for label in np.unique(p):
                    indices.append(i)
                    labels.append(label)
                    levels.append(clevels[i])
                    L.append(p == label)
            self._assignment = np.vstack(L)
            self._labels = np.array(labels)
            self._levels = np.array(levels)

        self.terminals = terminals  # if terminal is None, a default array will be created by the setter

        ## build tree
        self._build(**kwargs)

        ## pick parents
        T = self.pick(top, **kwargs)

        return T

    def _build(self, **kwargs):
        """Finds all the direct parents for the clusters in partitions. This is the first 
        step of weave(). Subclasses can override this function to achieve different results.

        Parameters
        ----------
        cutoff : keyword argument (0.5 ~ 1.0, default=0.8)
            containment index cutoff for claiming parenthood. 

        Returns
        -------
        G : networkx.DiGraph
            
        """

        cutoff = kwargs.pop('cutoff', 0.8)

        assume_levels = self.assume_levels
        terminals = self.terminals
        n_nodes = self.n_terminals
        L = self._assignment
        labels = self._labels
        levels = self._levels

        n_sets = len(L)

        rng = range(n_sets)
        if assume_levels:
            gen = ((i, j) for i, j in iproduct(rng, rng) if levels[i] > levels[j])
        else:
            gen = ((i, j) for i, j in iproduct(rng, rng) if i != j)
    
        # find all potential parents
        LOGGER.timeit('_init')
        LOGGER.info('initializing the graph...')
        # calculate containment indices
        CI = containment_indices_boolean(L, L)
        G = nx.DiGraph()
        for i, j in gen:
            C = CI[i, j]
            na = (i, 0)
            nb = (j, 0)

            if not G.has_node(na):
                G.add_node(na, index=i, level=levels[i], label=labels[i])

            if not G.has_node(nb):
                G.add_node(nb, index=j, level=levels[j], label=labels[j])

            if C >= cutoff:
                if G.has_edge(na, nb):
                    C0 = G[na][nb]['weight']
                    if C > C0:
                        G.remove_edge(na, nb)
                    else:
                        continue
                
                #if not nx.has_path(G, nb, na):
                G.add_edge(nb, na, weight=C)

        LOGGER.report('graph initialized in %.2fs', '_init')

        # add a root node to the graph
        roots = []
        for node, indeg in G.in_degree():
            if indeg == 0:
                roots.append(node)

        if len(roots) > 1:
            root = (-1, 0)   # (-1, 0) will be changed to (0, 0) later
            G.add_node(root, index=-1, level=-1, label=True)
            for node in roots:
                G.add_edge(root, node, weight=1.)
        else:
            root = roots[0]

        # remove grandparents (redundant edges)
        LOGGER.timeit('_redundancy')
        LOGGER.info('removing redudant edges...')
        redundant = []

        for node in G.nodes():
            parents = [_ for _ in G.predecessors(node)]
            ancestors = [_ for _ in nx.ancestors(G, node)]

            for a in parents:
                for b in ancestors:
                    if neq(a, b) and G.has_edge(a, b):
                        # a is a grandparent
                        redundant.append((a, node))
                        break

        # rcl = getrecursionlimit()
        # if rcl < RECURSION_MAX_DEPTH:
        #     setrecursionlimit(RECURSION_MAX_DEPTH)

        # for u, v in G.edges():
        #     if n_simple_paths(G, u, v) > 1:
        #         redundant.append((u, v))

        # setrecursionlimit(rcl)

        #nx.write_edgelist(G, 'sample_granny_network.txt')
        G.remove_edges_from(redundant)
        LOGGER.report('redundant edges removed in %.2fs', '_redundancy')


        # attach graph nodes to nodes in G
        # for efficiency purposes, this is done after redundant edges are 
        # removed. So we need to make sure we don't introduce new redundancy
        LOGGER.timeit('_attach')
        LOGGER.info('attaching terminal nodes to the graph...')
        X = np.arange(n_nodes)
        nodes = [node for node in G.nodes]
        attached_record = defaultdict(list)

        for node in nodes:
            n = node[0]
            if n == -1:
                continue

            x = X[L[n]]

            for i in x:
                ter = denumpize(terminals[i])
                attached = attached_record[ter]
                
                skip = False
                if attached:
                    for other in reversed(attached):
                        if nx.has_path(G, node, other): # other is a descendant of node, skip
                            skip = True; break
                        elif nx.has_path(G, other, node): # node is a descendant of other, remove other
                            attached.remove(other)
                            G.remove_edge(other, ter)
                    
                if not skip:
                    G.add_edge(node, ter, weight=1.)
                    attached.append(node)

        LOGGER.report('terminal nodes attached in %.2fs', '_attach')

        self._full = G
        
        # find secondary edges
        def node_size(node):
            if istuple(node):
                i = node[0]
                return np.count_nonzero(L[i])
            else:
                return 1

        LOGGER.timeit('_sec')
        LOGGER.info('finding secondary edges...')
        secondary_edges = []
        secondary_terminal_edges = []
        
        for node in G.nodes():
            parents = [_ for _ in G.predecessors(node)]
            if len(parents) > 1:
                nsize = node_size(node)
                if istuple(node):
                    pref = []
                    for p in parents:
                        w = G.edges()[p, node]['weight'] 
                        psize = node_size(p)
                        usize = w * nsize
                        j = usize / (nsize + psize - usize)
                        pref.append(j)

                    # weight (CI) * node_size gives the size of the union between the node and the parent
                    ranked_edges = [((x[0], node), x[1]) for x in sorted(zip(parents, pref), 
                                                key=lambda x: x[1], reverse=True)]
                    secondary_edges.extend(ranked_edges[1:])
                else:
                    edges = []
                    for p in parents:
                        #psize = node_size(p)
                        n_steps = nx.shortest_path_length(G, root, p)
                        edges.append(((p, node), n_steps))

                    edges = sorted(edges, key=lambda x: x[1], reverse=True)
                    secondary_terminal_edges.extend(edges[1:])

        secondary_edges.sort(key=lambda x: x[1], reverse=True)
        secondary_terminal_edges.sort(key=lambda x: x[1], reverse=True)

        self._secondary_edges = secondary_edges
        self._secondary_terminal_edges = secondary_terminal_edges
        LOGGER.report('secondary edges found in %.2fs', '_sec')

        return G

    def pick(self, percentage_edges, percentage_terminal_edges=0, **kwargs):
        """Picks top x percent edges. Alternative edges are ranked based on the number of 
        overlap terminal nodes between the child and the parent. This is the second 
        step of weave(). Subclasses can override this function to achieve different results.

        Parameters
        ----------
        percentage_edges : positional argument (0 ~ 100)
            top x percent (alternative) non-terminal edges to be kept in the hierarchy. This parameter 
            controls the number of parents each node has based on a global ranking of the 
            edges. Note that if set to 0 then each node will only have exactly one parent 
            (except for the root which has none). 
        
        percentage_terminal_edges : keyword argument (0 ~ 100)
            similar to ``percentage_edges`` but for top x percent (alternative) **terminal** edges 
            to be kept in the hierarchy. 

        Returns
        -------
        networkx.DiGraph
            
        """

        if self._secondary_edges is None:
            raise ValueError('hierarchy not built. Call weave() first')

        if self._secondary_terminal_edges is None:
            raise ValueError('hierarchy not built. Call weave() first')
            
        add_edges = kwargs.pop('additional', None)
        replace = kwargs.pop('replace', False)

        G = self._full
        T = self._full.copy()

        #W = [x[1] for x in self._secondary]
        secondary = [x[0] for x in self._secondary_edges]
        sectereg = [y[0] for y in self._secondary_terminal_edges]

        if percentage_edges == 0:
            # special treatment for one-parent case for better performance
            T.remove_edges_from(secondary)
        elif percentage_edges < 100:
            n = int(len(secondary) * percentage_edges/100.)
            removed_edges = secondary[n:]
            T.remove_edges_from(removed_edges)
        
        if percentage_terminal_edges == 0:
            # special treatment for one-parent case for better performance
            T.remove_edges_from(sectereg)
        elif percentage_terminal_edges < 100:
            m = int(len(sectereg) * percentage_terminal_edges/100.)
            removed_edges = sectereg[m:]
            T.remove_edges_from(removed_edges)

        if add_edges is not None:
            for u, v in add_edges:
                if not G.has_edge(u, v):
                    raise ValueError('edge does not exist: (%s, %s)'%(u, v))
                
                if replace:
                    edges_to_be_removed = []
                    for w in T.predecessors(v):
                        edges_to_be_removed.append((w, v))
                    T.remove_edges_from(edges_to_be_removed)
                T.add_edge(u, v, weight=1.)

        # prune tree
        self.hier = prune(T, **kwargs)

        # update attributes
        self.update_depth()
        #self.relabel()
        
        return self.hier

    def get_root(self):
        G = self.hier
        if G is None:
            raise ValueError('hierarchy not built. Call weave() first')

        return get_root(G)

    root = property(get_root, 'the root node')   
    
    def update_depth(self):
        if self.hier is None:
            raise ValueError('hierarchy not built. Call weave() first')

        T = self.hier

        def _update_topdown(parent):
            Q = [parent]

            while Q:
                parent = Q.pop(0)
                par_depth = T.nodes[parent]['depth']

                for child in T.successors(parent):
                    if 'depth' in T.nodes[child]:  # visited
                        ch_depth = T.nodes[child]['depth']
                        if ch_depth <= par_depth + 1:  # already shallower
                            continue
                    
                    T.nodes[child]['depth'] = par_depth + 1
                    Q.append(child)

        # update depths topdown
        root = self.root
        T.nodes[root]['depth'] = 0
        _update_topdown(root)

    def update_depthr(self):
        if self.hier is None:
            raise ValueError('hierarchy not built. Call weave() first')

        T = self.hier

        def _update_bottomup(children):
            Q = [child for child in children]

            while Q:
                child = Q.pop(0)
                ch_depthr = T.nodes[child]['depthr']

                for parent in T.predecessors(child):
                    if 'depthr' in T.nodes[parent]:  # visited
                        par_depthr = T.nodes[parent]['depthr']
                        if par_depthr >= ch_depthr - 1:  # already shallower
                            continue
                    
                    T.nodes[parent]['depthr'] = ch_depthr - 1
                    Q.append(parent)

        # update depths topdown
        for ter in self.terminals:
            T.nodes[ter]['depthr'] = 0
        _update_bottomup(self.terminals)

    def get_attribute(self, attr, node=None):
        if self.hier is None:
            raise ValueError('hierarchy not built. Call weave() first')
            
        G = self.hier

        # obtain values
        values = nx.get_node_attributes(G, attr)

        # pack results
        if node is None:
            ret = values
        elif istuple(node) or np.isscalar(node):
            ret = values.pop(node)
        else:
            ret = []
            for n in node:
                value = values[n] if n in values else None
                ret.append(value)

        return ret

    def depth(self, node=None):

        return self.get_attribute('depth', node)

    def level(self, node=None):

        return self.get_attribute('level', node)

    def get_max_depth(self):
        depths = self.depth(self.terminals)

        return np.max(depths)

    maxdepth = property(get_max_depth, 'the maximum depth of nodes to the root')   

    def all_depths(self, leaf=True):
        depth_dict = self.depth()

        if leaf:
            depths = [depth_dict[k] for k in depth_dict]
        else:
            depths = [depth_dict[k] for k in depth_dict if istuple(k)]

        return np.unique(depths)

    def show(self, **kwargs):
        """Visualize the hierarchy using networkx/graphviz hierarchical layouts.

        See Also
        --------
        show_hierarchy
            
        """

        nodelist = kwargs.pop('nodelist', None)
        dummy = kwargs.pop('dummy', False)

        if self.hier is None:
            raise ValueError('hierarchy not built. Call weave() first')

        T = self.hier

        if nodelist is None:
            nodelist = T.nodes()

        if dummy:
            T = stuff_dummies(T)
        
        return show_hierarchy(T, nodelist=nodelist, **kwargs)

    def node_cluster(self, node, out=None):
        """Recovers the cluster represented by a node in the hierarchy.

        Returns
        -------
        H : a Numpy array of labels for all the terminal nodes.
            
        """

        if self.hier is None:
            raise ValueError('hierarchy not built. Call weave() first')

        T = self.hier

        nodes = self.terminals
        n_nodes = self.n_terminals

        if out is None:
            out = np.zeros(n_nodes, dtype=bool)
        
        desc = [_ for _ in nx.descendants(T, node) 
                if not istuple(_)]
        
        if len(desc):
            for d in desc:
                j = find(nodes, d)

                out[j] = True
        else:
            j = find(nodes, node)
            out[j] = True

        return out

    def has_any_terminal(self, node):
        if self.hier is None:
            raise ValueError('hierarchy not built. Call weave() first')

        T = self.hier
        for child in T.successors(node):
            if not istuple(child):
                return True
        return False

    def _topdown_cluster(self, attr, value, **kwargs):
        """Recovers the partition at specified depth.

        Returns
        -------
        H : a Numpy array of labels for all the terminal nodes.
            
        """

        flat = kwargs.pop('flat', True) 
        stop_before_terminal = kwargs.pop('stop_before_terminal', True)

        if self.hier is None:
            raise ValueError('hierarchy not built. Call weave() first')

        T = self.hier
        n_nodes = self.n_terminals

        attrs = nx.get_node_attributes(T, attr)
        root = self.root

        # assign labels
        Q = [root]
        clusters = []
        visited = defaultdict(bool)

        while Q:
            node = Q.pop(0)

            if visited[node]:
                continue

            visited[node] = True

            if not istuple(node):
                if not stop_before_terminal:
                    clusters.append(node)
                continue

            if attrs[node] < value:
                if stop_before_terminal and self.has_any_terminal(node):
                         clusters.append(node)
                for child in T.successors(node):
                    if stop_before_terminal and not istuple(child):
                        continue
                    Q.append(child)
            elif attrs[node] == value:
                clusters.append(node)
            else:
                LOGGER.warn('something went wrong: visiting node with '
                            '%s greater than %d'%(attr, value))

        H = np.zeros((len(clusters), n_nodes), dtype=bool)

        for i, node in enumerate(clusters):
            self.node_cluster(node, H[i, :])

        if flat:
            I = np.arange(len(clusters)) + 1
            I = np.atleast_2d(I)
            H = H * I.T
            h = H.max(axis=0)
            return h
        
        return H

    def depth_cluster(self, depth, **kwargs):
        """Recovers the partition at specified depth.

        Returns
        -------
        H : a Numpy array of labels for all the terminal nodes.
            
        """
        
        return self._topdown_cluster('depth', depth, **kwargs)

    def level_cluster(self, level, **kwargs):
        """Recovers the partition that specified by the index based on the 
        hierarchy.

        Returns
        -------
        H : a Numpy array of labels for all the terminal nodes.
            
        """

        if not self.assume_levels:
            LOGGER.warn('Levels were not followed when building the hierarchy.')

        return self._topdown_cluster('level', level, **kwargs)

    def write(self, filename, format='ddot'):
        """Writes the hierarchy to a text file.

        Parameters
        ----------
        filename : the path and name of the output file.

        format : output format. Available options are "ddot".
            
        """

        if self.hier is None:
            raise ValueError('hierarchy not built. Call weave() first')

        G = self.hier.copy()
        
        if format == 'ddot':
            for u, v in G.edges():
                if istuple(u) and istuple(v):
                    G[u][v]['type'] = 'Child-Parent'
                else:
                    G[u][v]['type'] = 'Gene-Term'

        mapping = {}
        for node in internals(G):
            strnode = '%s_%s'%node
            mapping[node] = strnode

        G = nx.relabel_nodes(G, mapping, copy=False)

        with open(filename, 'w') as f:
            f.write('Parent\tChild\tType\n')
        
        with open(filename, 'ab') as f:
            nx.write_edgelist(G, f, delimiter='\t', data=['type'])

def containment_indices_legacy(A, B):
    from collections import defaultdict

    n = len(A)
    counterA  = defaultdict(int)
    counterB  = defaultdict(int)
    counterAB = defaultdict(int)

    for i in range(n):
        a = A[i]; b = B[i]
        
        counterA[a] += 1
        counterB[b] += 1
        counterAB[(a, b)] += 1

    LA = [l for l in counterA]
    LB = [l for l in counterB]

    CI = np.zeros((len(LA), len(LB)))
    for i, a in enumerate(LA):
        for j, b in enumerate(LB):
            CI[i, j] = counterAB[(a, b)] / counterA[a]

    return CI, LA, LB

def containment_indices(A, B):
    from collections import defaultdict

    A = np.asarray(A)
    B = np.asarray(B)

    LA = np.unique(A)
    LB = np.unique(B)

    bA = np.zeros((len(LA), len(A)))
    for i, a in enumerate(LA):
        bA[i] = A == a
    
    bB = np.zeros((len(LB), len(B)))
    for i, b in enumerate(LB):
        bB[i] = B == b

    overlap = bA.dot(bB.T)
    count = bA.sum(axis=1)
    CI = overlap / count[:, None]

    return CI, LA, LB

def containment_indices_boolean(A, B):
    count = np.count_nonzero(A, axis=1)

    A = A.astype(float)
    B = B.astype(float)
    overlap = A.dot(B.T)
    
    CI = overlap / count[:, None]
    return CI

def containment_indices_sparse(A, B, sparse=False):
    '''
    calculate containment index for all clusters in A in all clusters in B
    :param A: a numpy matrix, axis 0 - cluster; axis 1 - nodes
    :param B: a numpy matrix, axis 0 - cluster; axis 1 - nodes
    :return: a sparse matrix with containment index; calling row/column/data for individual pairs
    '''
    if not sparse:
        Asp = sp.sparse.csr_matrix(A)
        Bsp = sp.sparse.csr_matrix(B)
    else:
        Asp = A
        Bsp = B
    both = np.asarray(np.sum(Asp.multiply(Bsp), axis=1)).ravel()
    countA =  Asp.getnnz(axis=1) # this is dense matrix
    contain = 1.0 * both/countA
    # print(both, countA, contain)
    return contain

def all_equal(iterable):
    "Returns True if all the elements are equal to each other"

    from itertools import groupby

    g = groupby(iterable)
    return next(g, True) and not next(g, False)

def boolize(x):
    "Converts x to boolean"

    if not isinstance(x, bool):
        x = bool(int(x))
    return x

def neq(a, b):
    # workaround for a (potential) 
    # networkx bug related to numpy.int, 
    # e.g. (1, 2) == numpy.int32(1)

    r = a != b
    try:
        len(r)
    except TypeError:
        return True
    return r

def n_simple_paths(G, u, v):
    nsp_reg = {}

    def nsp(u, v):
        if u == v:
            return 1
        if not u in nsp_reg:
            npaths = 0
            for c in G.successors(u):
                npaths += nsp(c, v)
            nsp_reg[u] = npaths

        return nsp_reg[u]

    return nsp(u, v)

def denumpize(x):
    if isinstance(x, np.generic):
        return x.item()
    return x

def find(A, a):
    if isinstance(A, list):
        return A.index(a)
    else:
        A = np.asarray(A)
        return np.where(A==a)[0][0]

def get_root(T):
    for node, indeg in T.in_degree():
        if indeg == 0:
            return node

    return None

def prune(T, **kwargs):
    """Removes the nodes with only one child and the nodes that have no terminal 
    nodes (e.g. genes) as descendants."""

    strict_single_branch = kwargs.pop('strict_single_branch', False)
    # prune tree
    # remove dead-ends
    internal_nodes = [node for node in T.nodes() if istuple(node)]
    out_degrees = [val for key, val in T.out_degree(internal_nodes)]

    while (0 in out_degrees):
        for node in reversed(internal_nodes):
            outdeg = T.out_degree(node)
            if istuple(node) and outdeg == 0:
                T.remove_node(node)
                internal_nodes.remove(node)

        out_degrees = [val for key, val in T.out_degree(internal_nodes)]

    # remove single branches
    def _single_branch(node):
        indeg = T.in_degree(node)

        if indeg != 1:
            return False

        if strict_single_branch:
            outdeg = T.out_degree(node)
            if outdeg == 1:
                child = next(T.successors(node))
                if not istuple(child):
                    outdeg = 0
        else: # is a single branch if there is only one internal outedge
            outdeg = 0
            for child in T.successors(node):
                if istuple(child):
                    outdeg += 1

        if outdeg > 1:
            return False
        elif outdeg == 0:
            outdeg = T.out_degree(node)
            if outdeg != 1:
                return False

        return True

    #all_nodes = [node for node in T.nodes()]
    all_nodes = [node for node in traverse_topdown(T)]

    for node in all_nodes:
        if _single_branch(node):
            parent = next(T.predecessors(node))
            children = [child for child in T.successors(node)]
            
            w1 = T[parent][node]['weight']
            
            for child in children:
                w2 = T[node][child]['weight']
                T.add_edge(parent, child, weight=w1 + w2)

            T.remove_node(node)

    return T

def traverse_topdown(T, mode='breadth'):
    if mode == 'depth':
        q = -1
    elif mode == 'breadth':
        q = 0
    else:
        raise ValueError('mode must be either "depth" or "breadth"')

    root = get_root(T)
    Q = [root]
    visited = defaultdict(bool)
    while Q:
        node = Q.pop(q)

        if visited[node]:
            continue
        
        visited[node] = True

        for child in T.successors(node):
            Q.append(child)
        yield node

def stuff_dummies(hierarchy):
    """Puts dummy nodes into the hierarchy. The dummy nodes are used 
    in `show_hierarchy` when `assume_level` is True.

    Returns
    -------
    T : networkx.DiGraph
        An hierarchy with dummy nodes added.

    Raises
    ------
    ValueError
        If hierarchy has not been built.

    """

    T = hierarchy.copy()

    level_dict = nx.get_node_attributes(T, 'level')
    levels = np.unique(list(level_dict.values()))
    d = -1

    # make a list of node refs for the iteration during which nodes will change
    internal_nodes = [node for node in internals(T) if T.in_degree(node)]

    for node in internal_nodes:
        level = T.nodes[node]['level']
        i = find(levels, level)
        parents = [_ for _ in T.predecessors(node)]
        for parent in parents:
            if not istuple(parent):
                # unlikely to happen
                continue
            plevel = T.nodes[parent]['level']
            j = find(levels, plevel)
            n = i - j
            if n > 1:
                # add n-1 dummies
                T.remove_edge(parent, node)
                #d0 = None
                for i in range(1, n):
                    d += 1
                    l = levels[j + i]
                    #labels = [n[1] for n in T.nodes() if istuple(n) if T.nodes[n]['level']==l]
                    #d = getSmallestAvailable(labels)
                    
                    curr = (l, d, None)
                    if i == 1:
                        T.add_edge(parent, curr, weight=1)
                    else:
                        l0 = levels[j + i - 1]
                        T.add_edge((l0, d-1, None), curr, weight=1)
                    #d0 = d
                    T.nodes[curr]['level'] = l
                    #T.nodes[curr]['level'] = None

                T.add_edge(curr, node, weight=1)
    
    return T

def show_hierarchy(T, **kwargs):
    """Visualizes the hierarchy"""

    from networkx.drawing.nx_pydot import write_dot, graphviz_layout
    from networkx import draw, get_edge_attributes, draw_networkx_edge_labels
    from os import name as osname

    from matplotlib.pyplot import plot, xlim, ylim

    style = kwargs.pop('style', 'dot')
    leaf = kwargs.pop('leaf', True)
    nodesize = kwargs.pop('node_size', 16)
    edgescale = kwargs.pop('edge_scale', None)
    edgelabel = kwargs.pop('edge_label', False)
    interactive = kwargs.pop('interactive', True)
    excluded_nodes = kwargs.pop('excluded_nodes', [])

    isWindows = osname == 'nt'

    if isWindows:
        style += '.exe'

    if not leaf:
        T2 = T.subgraph(n for n in T.nodes() if istuple(n) and n not in excluded_nodes)
        if 'nodelist' in kwargs:
            nodes = kwargs.pop('nodelist')
            nonleaves = []
            for node in nodes:
                if istuple(node) and node not in excluded_nodes:
                    nonleaves.append(node)
            kwargs['nodelist'] = nonleaves
    else:
        T2 = T.subgraph(n for n in T.nodes() if n not in excluded_nodes)
    
    pos = graphviz_layout(T2, prog=style)

    if edgescale:
        widths = []
        for u, v in T2.edges():
            w = T2[u][v]['weight']*edgescale
            widths.append(w) 
    else:
        widths = 1.

    if not 'arrows' in kwargs:
        kwargs['arrows'] = False
        
    draw(T2, pos, node_size=nodesize, width=widths, **kwargs)
    if edgelabel:
        labels = get_edge_attributes(T2, 'weight')
        draw_networkx_edge_labels(T2, pos, edge_labels=labels)

    if interactive:
        from matplotlib.pyplot import gcf
        from scipy.spatial.distance import cdist

        annotations = {}

        def _onclick(event):
            ax = event.inaxes
            if ax is not None:
                if event.button == 1:
                    view = ax.viewLim.extents
                    xl = (view[0], view[2])
                    yl = (view[1], view[3])
                    xl, yl = ax.get_xlim(), ax.get_ylim()
                    x_, y_ = event.xdata, event.ydata

                    dx = (xl[1] - xl[0]) / 20 
                    dy = (yl[1] - yl[0]) / 20
                    dr = min((dx, dy))

                    nodes = []; coords = []
                    for node, coord in pos.items():
                        nodes.append(node)
                        coords.append(coord)

                    D = cdist([(x_, y_)], coords).flatten()
                    i = D.argmin()

                    if D[i] < dr:
                        x, y = coords[i]
                        node = nodes[i]
                        
                        if node not in annotations:
                            #ax.plot([i, i], ax.get_ylim(), 'k--')
                            l = ax.plot([x], [y], 'bo', fillstyle='none', markersize=nodesize//2)
                            t = ax.text(x, y, str(node), color='k')
                            annotations[node] = (l[0], t)
                            xlim(xl); ylim(yl)
                        else:
                            l, t = annotations.pop(node)
                            ax.lines.remove(l)
                            ax.texts.remove(t) 
                elif event.button == 3:
                    for node in annotations:
                        l, t = annotations[node]
                        ax.lines.remove(l)
                        ax.texts.remove(t)
                    annotations.clear()
                fig.canvas.draw()

        fig = gcf()
        cid = fig.canvas.mpl_connect('button_press_event', _onclick)

    return T2, pos

def weave(partitions, terminals=None, **kwargs):
    weaver = Weaver()
    weaver.weave(partitions, terminals, **kwargs)

    return weaver

if __name__ == '__main__':
    from pylab import *

    # L = ['11111111',
    #      '11111100',
    #      '00001111',
    #      '11100000',
    #      '00110000',
    #      '00001100',
    #      '00000011']

    # L = [list(p) for p in L]
    # nodes = 'ABCDEFGH'

    # weaver = Weaver(L, boolean=True, terminals=nodes, assume_levels=False)
    # weaver.weave(cutoff=0.9, top=100)
    # weaver.pick(0)

    # ion()
    # figure()
    # weaver.show()

    P = ['11111111',
         '11111100',
         '00001111',
         '11100000',
         '00110000',
         '00001100',
         '00000011']
    P = [list(p) for p in P]

    nodes = 'ABCDEFGH'

    w = Weaver()
    T = w.weave(P, boolean=True, terminals=nodes, cutoff=0.9, top=10)
