import{d as K,f as M,y as Xe,x as ge,i as bt,A as v,K as mn,l as We,s as yn,u as Cn,B as le,n as pt,D as wn,w as Oe,p as _t,I as Et,F as xn,H as Sn,z as kn,L as Rn}from"./_plugin-vue_export-helper-GEDmboVY.js";import{x as Fe,a1 as wt,R as At,Q as $t,I as ht,bd as Pn,T as qe,an as Ne,bq as lt,Z as ze,br as mt,t as Tn,n as H,p as j,o as te,q as de,r as Te,aO as Nt,a0 as Ye,am as On,S as Fn,ad as In,y as Qe,ac as yt,A as xe,a4 as et,bs as zn,G as oe,a6 as Me,bt as Mn,j as Bn,bu as _n,ai as Z,w as En,F as ve,bv as xt,m as An,bw as $n,bx as Nn,L as Ln,by as Wn,aJ as Kn,aK as Dn,aL as Hn,aP as vt,aM as St,z as kt,v as jn,D as Vn,E as Un,aN as Gn,aR as qn,a5 as Xn,bz as Yn}from"./index-Cao6ea7M.js";import{N as Zn}from"./FormField.vue_vue_type_style_index_0_scoped_ab743373_lang-DMrNP1ey.js";function Le(e,n){let{target:t}=e;for(;t;){if(t.dataset&&t.dataset[n]!==void 0)return!0;t=t.parentElement}return!1}function Rt(e){return e&-e}class Lt{constructor(n,t){this.l=n,this.min=t;const o=new Array(n+1);for(let r=0;r<n+1;++r)o[r]=0;this.ft=o}add(n,t){if(t===0)return;const{l:o,ft:r}=this;for(n+=1;n<=o;)r[n]+=t,n+=Rt(n)}get(n){return this.sum(n+1)-this.sum(n)}sum(n){if(n===void 0&&(n=this.l),n<=0)return 0;const{ft:t,min:o,l:r}=this;if(n>r)throw new Error("[FinweckTree.sum]: `i` is larger than length.");let s=n*o;for(;n>0;)s+=t[n],n-=Rt(n);return s}getBound(n){let t=0,o=this.l;for(;o>t;){const r=Math.floor((t+o)/2),s=this.sum(r);if(s>n){o=r;continue}else if(s<n){if(t===r)return this.sum(t+1)<=n?t+1:r;t=r}else return r}return t}}let Ge;function Jn(){return typeof document>"u"?!1:(Ge===void 0&&("matchMedia"in window?Ge=window.matchMedia("(pointer:coarse)").matches:Ge=!1),Ge)}let it;function Pt(){return typeof document>"u"?1:(it===void 0&&(it="chrome"in window?window.devicePixelRatio:1),it)}const Wt="VVirtualListXScroll";function Qn({columnsRef:e,renderColRef:n,renderItemWithColsRef:t}){const o=M(0),r=M(0),s=K(()=>{const c=e.value;if(c.length===0)return null;const p=new Lt(c.length,0);return c.forEach((g,R)=>{p.add(R,g.width)}),p}),l=Fe(()=>{const c=s.value;return c!==null?Math.max(c.getBound(r.value)-1,0):0}),a=c=>{const p=s.value;return p!==null?p.sum(c):0},d=Fe(()=>{const c=s.value;return c!==null?Math.min(c.getBound(r.value+o.value)+1,e.value.length-1):0});return Xe(Wt,{startIndexRef:l,endIndexRef:d,columnsRef:e,renderColRef:n,renderItemWithColsRef:t,getLeft:a}),{listWidthRef:o,scrollLeftRef:r}}const Tt=ge({name:"VirtualListRow",props:{index:{type:Number,required:!0},item:{type:Object,required:!0}},setup(){const{startIndexRef:e,endIndexRef:n,columnsRef:t,getLeft:o,renderColRef:r,renderItemWithColsRef:s}=bt(Wt);return{startIndex:e,endIndex:n,columns:t,renderCol:r,renderItemWithCols:s,getLeft:o}},render(){const{startIndex:e,endIndex:n,columns:t,renderCol:o,renderItemWithCols:r,getLeft:s,item:l}=this;if(r!=null)return r({itemIndex:this.index,startColIndex:e,endColIndex:n,allColumns:t,item:l,getLeft:s});if(o!=null){const a=[];for(let d=e;d<=n;++d){const c=t[d];a.push(o({column:c,left:s(d),item:l}))}return a}return null}}),eo=qe(".v-vl",{maxHeight:"inherit",height:"100%",overflow:"auto",minWidth:"1px"},[qe("&:not(.v-vl--show-scrollbar)",{scrollbarWidth:"none"},[qe("&::-webkit-scrollbar, &::-webkit-scrollbar-track-piece, &::-webkit-scrollbar-thumb",{width:0,height:0,display:"none"})])]),to=ge({name:"VirtualList",inheritAttrs:!1,props:{showScrollbar:{type:Boolean,default:!0},columns:{type:Array,default:()=>[]},renderCol:Function,renderItemWithCols:Function,items:{type:Array,default:()=>[]},itemSize:{type:Number,required:!0},itemResizable:Boolean,itemsStyle:[String,Object],visibleItemsTag:{type:[String,Object],default:"div"},visibleItemsProps:Object,ignoreItemResize:Boolean,onScroll:Function,onWheel:Function,onResize:Function,defaultScrollKey:[Number,String],defaultScrollIndex:Number,keyField:{type:String,default:"key"},paddingTop:{type:[Number,String],default:0},paddingBottom:{type:[Number,String],default:0}},setup(e){const n=$t();eo.mount({id:"vueuc/virtual-list",head:!0,anchorMetaName:At,ssr:n}),We(()=>{const{defaultScrollIndex:h,defaultScrollKey:T}=e;h!=null?F({index:h}):T!=null&&F({key:T})});let t=!1,o=!1;yn(()=>{if(t=!1,!o){o=!0;return}F({top:I.value,left:l.value})}),Cn(()=>{t=!0,o||(o=!0)});const r=Fe(()=>{if(e.renderCol==null&&e.renderItemWithCols==null||e.columns.length===0)return;let h=0;return e.columns.forEach(T=>{h+=T.width}),h}),s=K(()=>{const h=new Map,{keyField:T}=e;return e.items.forEach((D,B)=>{h.set(D[T],B)}),h}),{scrollLeftRef:l,listWidthRef:a}=Qn({columnsRef:le(e,"columns"),renderColRef:le(e,"renderCol"),renderItemWithColsRef:le(e,"renderItemWithCols")}),d=M(null),c=M(void 0),p=new Map,g=K(()=>{const{items:h,itemSize:T,keyField:D}=e,B=new Lt(h.length,T);return h.forEach((Q,V)=>{const X=Q[D],G=p.get(X);G!==void 0&&B.add(V,G)}),B}),R=M(0),I=M(0),m=Fe(()=>Math.max(g.value.getBound(I.value-ht(e.paddingTop))-1,0)),w=K(()=>{const{value:h}=c;if(h===void 0)return[];const{items:T,itemSize:D}=e,B=m.value,Q=Math.min(B+Math.ceil(h/D+1),T.length-1),V=[];for(let X=B;X<=Q;++X)V.push(T[X]);return V}),F=(h,T)=>{if(typeof h=="number"){b(h,T,"auto");return}const{left:D,top:B,index:Q,key:V,position:X,behavior:G,debounce:Y=!0}=h;if(D!==void 0||B!==void 0)b(D,B,G);else if(Q!==void 0)O(Q,G,Y);else if(V!==void 0){const ae=s.value.get(V);ae!==void 0&&O(ae,G,Y)}else X==="bottom"?b(0,Number.MAX_SAFE_INTEGER,G):X==="top"&&b(0,0,G)};let P,k=null;function O(h,T,D){const B=d.value;if(B==null)return;const{value:Q}=g,V=Q.sum(h)+ht(e.paddingTop);if(!D)B.scrollTo({left:0,top:V,behavior:T});else{P=h,k!==null&&window.clearTimeout(k),k=window.setTimeout(()=>{P=void 0,k=null},16);const{scrollTop:X,offsetHeight:G}=B;if(V>X){const Y=Q.get(h);V+Y<=X+G||B.scrollTo({left:0,top:V+Y-G,behavior:T})}else B.scrollTo({left:0,top:V,behavior:T})}}function b(h,T,D){const B=d.value;B?.scrollTo({left:h,top:T,behavior:D})}function y(h,T){var D,B,Q;if(t||e.ignoreItemResize||ie(T.target))return;const{value:V}=g,X=s.value.get(h),G=V.get(X),Y=(Q=(B=(D=T.borderBoxSize)===null||D===void 0?void 0:D[0])===null||B===void 0?void 0:B.blockSize)!==null&&Q!==void 0?Q:T.contentRect.height;if(Y===G)return;Y-e.itemSize===0?p.delete(h):p.set(h,Y-e.itemSize);const se=Y-G;if(se===0)return;V.add(X,se);const u=d.value;if(u!=null){if(P===void 0){const x=V.sum(X);u.scrollTop>x&&u.scrollBy(0,se)}else if(X<P)u.scrollBy(0,se);else if(X===P){const x=V.sum(X);Y+x>u.scrollTop+u.offsetHeight&&u.scrollBy(0,se)}ee()}R.value++}const A=!Jn();let _=!1;function U(h){var T;(T=e.onScroll)===null||T===void 0||T.call(e,h),(!A||!_)&&ee()}function q(h){var T;if((T=e.onWheel)===null||T===void 0||T.call(e,h),A){const D=d.value;if(D!=null){if(h.deltaX===0&&(D.scrollTop===0&&h.deltaY<=0||D.scrollTop+D.offsetHeight>=D.scrollHeight&&h.deltaY>=0))return;h.preventDefault(),D.scrollTop+=h.deltaY/Pt(),D.scrollLeft+=h.deltaX/Pt(),ee(),_=!0,Pn(()=>{_=!1})}}}function E(h){if(t||ie(h.target))return;if(e.renderCol==null&&e.renderItemWithCols==null){if(h.contentRect.height===c.value)return}else if(h.contentRect.height===c.value&&h.contentRect.width===a.value)return;c.value=h.contentRect.height,a.value=h.contentRect.width;const{onResize:T}=e;T!==void 0&&T(h)}function ee(){const{value:h}=d;h!=null&&(I.value=h.scrollTop,l.value=h.scrollLeft)}function ie(h){let T=h;for(;T!==null;){if(T.style.display==="none")return!0;T=T.parentElement}return!1}return{listHeight:c,listStyle:{overflow:"auto"},keyToIndex:s,itemsStyle:K(()=>{const{itemResizable:h}=e,T=Ne(g.value.sum());return R.value,[e.itemsStyle,{boxSizing:"content-box",width:Ne(r.value),height:h?"":T,minHeight:h?T:"",paddingTop:Ne(e.paddingTop),paddingBottom:Ne(e.paddingBottom)}]}),visibleItemsStyle:K(()=>(R.value,{transform:`translateY(${Ne(g.value.sum(m.value))})`})),viewportItems:w,listElRef:d,itemsElRef:M(null),scrollTo:F,handleListResize:E,handleListScroll:U,handleListWheel:q,handleItemResize:y}},render(){const{itemResizable:e,keyField:n,keyToIndex:t,visibleItemsTag:o}=this;return v(wt,{onResize:this.handleListResize},{default:()=>{var r,s;return v("div",mn(this.$attrs,{class:["v-vl",this.showScrollbar&&"v-vl--show-scrollbar"],onScroll:this.handleListScroll,onWheel:this.handleListWheel,ref:"listElRef"}),[this.items.length!==0?v("div",{ref:"itemsElRef",class:"v-vl-items",style:this.itemsStyle},[v(o,Object.assign({class:"v-vl-visible-items",style:this.visibleItemsStyle},this.visibleItemsProps),{default:()=>{const{renderCol:l,renderItemWithCols:a}=this;return this.viewportItems.map(d=>{const c=d[n],p=t.get(c),g=l!=null?v(Tt,{index:p,item:d}):void 0,R=a!=null?v(Tt,{index:p,item:d}):void 0,I=this.$slots.default({item:d,renderedCols:g,renderedItemWithCols:R,index:p})[0];return e?v(wt,{key:c,onResize:m=>this.handleItemResize(c,m)},{default:()=>I}):(I.key=c,I)})}})]):(s=(r=this.$slots).empty)===null||s===void 0?void 0:s.call(r)])}})}}),ye="v-hidden",no=qe("[v-hidden]",{display:"none!important"}),Ot=ge({name:"Overflow",props:{getCounter:Function,getTail:Function,updateCounter:Function,onUpdateCount:Function,onUpdateOverflow:Function},setup(e,{slots:n}){const t=M(null),o=M(null);function r(l){const{value:a}=t,{getCounter:d,getTail:c}=e;let p;if(d!==void 0?p=d():p=o.value,!a||!p)return;p.hasAttribute(ye)&&p.removeAttribute(ye);const{children:g}=a;if(l.showAllItemsBeforeCalculate)for(const O of g)O.hasAttribute(ye)&&O.removeAttribute(ye);const R=a.offsetWidth,I=[],m=n.tail?c?.():null;let w=m?m.offsetWidth:0,F=!1;const P=a.children.length-(n.tail?1:0);for(let O=0;O<P-1;++O){if(O<0)continue;const b=g[O];if(F){b.hasAttribute(ye)||b.setAttribute(ye,"");continue}else b.hasAttribute(ye)&&b.removeAttribute(ye);const y=b.offsetWidth;if(w+=y,I[O]=y,w>R){const{updateCounter:A}=e;for(let _=O;_>=0;--_){const U=P-1-_;A!==void 0?A(U):p.textContent=`${U}`;const q=p.offsetWidth;if(w-=I[_],w+q<=R||_===0){F=!0,O=_-1,m&&(O===-1?(m.style.maxWidth=`${R-q}px`,m.style.boxSizing="border-box"):m.style.maxWidth="");const{onUpdateCount:E}=e;E&&E(U);break}}}}const{onUpdateOverflow:k}=e;F?k!==void 0&&k(!0):(k!==void 0&&k(!1),p.setAttribute(ye,""))}const s=$t();return no.mount({id:"vueuc/overflow",head:!0,anchorMetaName:At,ssr:s}),We(()=>r({showAllItemsBeforeCalculate:!1})),{selfRef:t,counterRef:o,sync:r}},render(){const{$slots:e}=this;return pt(()=>this.sync({showAllItemsBeforeCalculate:!1})),v("div",{class:"v-overflow",ref:"selfRef"},[wn(e,"default"),e.counter?e.counter():v("span",{style:{display:"inline-block"},ref:"counterRef"}),e.tail?e.tail():null])}});function Kt(e,n){n&&(We(()=>{const{value:t}=e;t&&lt.registerHandler(t,n)}),Oe(e,(t,o)=>{o&&lt.unregisterHandler(o)},{deep:!1}),_t(()=>{const{value:t}=e;t&&lt.unregisterHandler(t)}))}function Ft(e){switch(typeof e){case"string":return e||void 0;case"number":return String(e);default:return}}function at(e){const n=e.filter(t=>t!==void 0);if(n.length!==0)return n.length===1?n[0]:t=>{e.forEach(o=>{o&&o(t)})}}const oo=ge({name:"Checkmark",render(){return v("svg",{xmlns:"http://www.w3.org/2000/svg",viewBox:"0 0 16 16"},v("g",{fill:"none"},v("path",{d:"M14.046 3.486a.75.75 0 0 1-.032 1.06l-7.93 7.474a.85.85 0 0 1-1.188-.022l-2.68-2.72a.75.75 0 1 1 1.068-1.053l2.234 2.267l7.468-7.038a.75.75 0 0 1 1.06.032z",fill:"currentColor"})))}}),ro=ge({props:{onFocus:Function,onBlur:Function},setup(e){return()=>v("div",{style:"width: 0; height: 0",tabindex:0,onFocus:e.onFocus,onBlur:e.onBlur})}});function It(e){return Array.isArray(e)?e:[e]}const gt={STOP:"STOP"};function Dt(e,n){const t=n(e);e.children!==void 0&&t!==gt.STOP&&e.children.forEach(o=>Dt(o,n))}function lo(e,n={}){const{preserveGroup:t=!1}=n,o=[],r=t?l=>{l.isLeaf||(o.push(l.key),s(l.children))}:l=>{l.isLeaf||(l.isGroup||o.push(l.key),s(l.children))};function s(l){l.forEach(r)}return s(e),o}function io(e,n){const{isLeaf:t}=e;return t!==void 0?t:!n(e)}function ao(e){return e.children}function so(e){return e.key}function co(){return!1}function uo(e,n){const{isLeaf:t}=e;return!(t===!1&&!Array.isArray(n(e)))}function fo(e){return e.disabled===!0}function ho(e,n){return e.isLeaf===!1&&!Array.isArray(n(e))}function st(e){var n;return e==null?[]:Array.isArray(e)?e:(n=e.checkedKeys)!==null&&n!==void 0?n:[]}function ct(e){var n;return e==null||Array.isArray(e)?[]:(n=e.indeterminateKeys)!==null&&n!==void 0?n:[]}function vo(e,n){const t=new Set(e);return n.forEach(o=>{t.has(o)||t.add(o)}),Array.from(t)}function go(e,n){const t=new Set(e);return n.forEach(o=>{t.has(o)&&t.delete(o)}),Array.from(t)}function bo(e){return e?.type==="group"}function po(e){const n=new Map;return e.forEach((t,o)=>{n.set(t.key,o)}),t=>{var o;return(o=n.get(t))!==null&&o!==void 0?o:null}}class mo extends Error{constructor(){super(),this.message="SubtreeNotLoadedError: checking a subtree whose required nodes are not fully loaded."}}function yo(e,n,t,o){return Ze(n.concat(e),t,o,!1)}function Co(e,n){const t=new Set;return e.forEach(o=>{const r=n.treeNodeMap.get(o);if(r!==void 0){let s=r.parent;for(;s!==null&&!(s.disabled||t.has(s.key));)t.add(s.key),s=s.parent}}),t}function wo(e,n,t,o){const r=Ze(n,t,o,!1),s=Ze(e,t,o,!0),l=Co(e,t),a=[];return r.forEach(d=>{(s.has(d)||l.has(d))&&a.push(d)}),a.forEach(d=>r.delete(d)),r}function dt(e,n){const{checkedKeys:t,keysToCheck:o,keysToUncheck:r,indeterminateKeys:s,cascade:l,leafOnly:a,checkStrategy:d,allowNotLoaded:c}=e;if(!l)return o!==void 0?{checkedKeys:vo(t,o),indeterminateKeys:Array.from(s)}:r!==void 0?{checkedKeys:go(t,r),indeterminateKeys:Array.from(s)}:{checkedKeys:Array.from(t),indeterminateKeys:Array.from(s)};const{levelTreeNodeMap:p}=n;let g;r!==void 0?g=wo(r,t,n,c):o!==void 0?g=yo(o,t,n,c):g=Ze(t,n,c,!1);const R=d==="parent",I=d==="child"||a,m=g,w=new Set,F=Math.max.apply(null,Array.from(p.keys()));for(let P=F;P>=0;P-=1){const k=P===0,O=p.get(P);for(const b of O){if(b.isLeaf)continue;const{key:y,shallowLoaded:A}=b;if(I&&A&&b.children.forEach(E=>{!E.disabled&&!E.isLeaf&&E.shallowLoaded&&m.has(E.key)&&m.delete(E.key)}),b.disabled||!A)continue;let _=!0,U=!1,q=!0;for(const E of b.children){const ee=E.key;if(!E.disabled){if(q&&(q=!1),m.has(ee))U=!0;else if(w.has(ee)){U=!0,_=!1;break}else if(_=!1,U)break}}_&&!q?(R&&b.children.forEach(E=>{!E.disabled&&m.has(E.key)&&m.delete(E.key)}),m.add(y)):U&&w.add(y),k&&I&&m.has(y)&&m.delete(y)}}return{checkedKeys:Array.from(m),indeterminateKeys:Array.from(w)}}function Ze(e,n,t,o){const{treeNodeMap:r,getChildren:s}=n,l=new Set,a=new Set(e);return e.forEach(d=>{const c=r.get(d);c!==void 0&&Dt(c,p=>{if(p.disabled)return gt.STOP;const{key:g}=p;if(!l.has(g)&&(l.add(g),a.add(g),ho(p.rawNode,s))){if(o)return gt.STOP;if(!t)throw new mo}})}),a}function xo(e,{includeGroup:n=!1,includeSelf:t=!0},o){var r;const s=o.treeNodeMap;let l=e==null?null:(r=s.get(e))!==null&&r!==void 0?r:null;const a={keyPath:[],treeNodePath:[],treeNode:l};if(l?.ignored)return a.treeNode=null,a;for(;l;)!l.ignored&&(n||!l.isGroup)&&a.treeNodePath.push(l),l=l.parent;return a.treeNodePath.reverse(),t||a.treeNodePath.pop(),a.keyPath=a.treeNodePath.map(d=>d.key),a}function So(e){if(e.length===0)return null;const n=e[0];return n.isGroup||n.ignored||n.disabled?n.getNext():n}function ko(e,n){const t=e.siblings,o=t.length,{index:r}=e;return n?t[(r+1)%o]:r===t.length-1?null:t[r+1]}function zt(e,n,{loop:t=!1,includeDisabled:o=!1}={}){const r=n==="prev"?Ro:ko,s={reverse:n==="prev"};let l=!1,a=null;function d(c){if(c!==null){if(c===e){if(!l)l=!0;else if(!e.disabled&&!e.isGroup){a=e;return}}else if((!c.disabled||o)&&!c.ignored&&!c.isGroup){a=c;return}if(c.isGroup){const p=Ct(c,s);p!==null?a=p:d(r(c,t))}else{const p=r(c,!1);if(p!==null)d(p);else{const g=Po(c);g?.isGroup?d(r(g,t)):t&&d(r(c,!0))}}}}return d(e),a}function Ro(e,n){const t=e.siblings,o=t.length,{index:r}=e;return n?t[(r-1+o)%o]:r===0?null:t[r-1]}function Po(e){return e.parent}function Ct(e,n={}){const{reverse:t=!1}=n,{children:o}=e;if(o){const{length:r}=o,s=t?r-1:0,l=t?-1:r,a=t?-1:1;for(let d=s;d!==l;d+=a){const c=o[d];if(!c.disabled&&!c.ignored)if(c.isGroup){const p=Ct(c,n);if(p!==null)return p}else return c}}return null}const To={getChild(){return this.ignored?null:Ct(this)},getParent(){const{parent:e}=this;return e?.isGroup?e.getParent():e},getNext(e={}){return zt(this,"next",e)},getPrev(e={}){return zt(this,"prev",e)}};function Oo(e,n){const t=n?new Set(n):void 0,o=[];function r(s){s.forEach(l=>{o.push(l),!(l.isLeaf||!l.children||l.ignored)&&(l.isGroup||t===void 0||t.has(l.key))&&r(l.children)})}return r(e),o}function Fo(e,n){const t=e.key;for(;n;){if(n.key===t)return!0;n=n.parent}return!1}function Ht(e,n,t,o,r,s=null,l=0){const a=[];return e.forEach((d,c)=>{var p;const g=Object.create(o);if(g.rawNode=d,g.siblings=a,g.level=l,g.index=c,g.isFirstChild=c===0,g.isLastChild=c+1===e.length,g.parent=s,!g.ignored){const R=r(d);Array.isArray(R)&&(g.children=Ht(R,n,t,o,r,g,l+1))}a.push(g),n.set(g.key,g),t.has(l)||t.set(l,[]),(p=t.get(l))===null||p===void 0||p.push(g)}),a}function Io(e,n={}){var t;const o=new Map,r=new Map,{getDisabled:s=fo,getIgnored:l=co,getIsGroup:a=bo,getKey:d=so}=n,c=(t=n.getChildren)!==null&&t!==void 0?t:ao,p=n.ignoreEmptyChildren?b=>{const y=c(b);return Array.isArray(y)?y.length?y:null:y}:c,g=Object.assign({get key(){return d(this.rawNode)},get disabled(){return s(this.rawNode)},get isGroup(){return a(this.rawNode)},get isLeaf(){return io(this.rawNode,p)},get shallowLoaded(){return uo(this.rawNode,p)},get ignored(){return l(this.rawNode)},contains(b){return Fo(this,b)}},To),R=Ht(e,o,r,g,p);function I(b){if(b==null)return null;const y=o.get(b);return y&&!y.isGroup&&!y.ignored?y:null}function m(b){if(b==null)return null;const y=o.get(b);return y&&!y.ignored?y:null}function w(b,y){const A=m(b);return A?A.getPrev(y):null}function F(b,y){const A=m(b);return A?A.getNext(y):null}function P(b){const y=m(b);return y?y.getParent():null}function k(b){const y=m(b);return y?y.getChild():null}const O={treeNodes:R,treeNodeMap:o,levelTreeNodeMap:r,maxLevel:Math.max(...r.keys()),getChildren:p,getFlattenedNodes(b){return Oo(R,b)},getNode:I,getPrev:w,getNext:F,getParent:P,getChild:k,getFirstAvailableNode(){return So(R)},getPath(b,y={}){return xo(b,y,O)},getCheckedKeys(b,y={}){const{cascade:A=!0,leafOnly:_=!1,checkStrategy:U="all",allowNotLoaded:q=!1}=y;return dt({checkedKeys:st(b),indeterminateKeys:ct(b),cascade:A,leafOnly:_,checkStrategy:U,allowNotLoaded:q},O)},check(b,y,A={}){const{cascade:_=!0,leafOnly:U=!1,checkStrategy:q="all",allowNotLoaded:E=!1}=A;return dt({checkedKeys:st(y),indeterminateKeys:ct(y),keysToCheck:b==null?[]:It(b),cascade:_,leafOnly:U,checkStrategy:q,allowNotLoaded:E},O)},uncheck(b,y,A={}){const{cascade:_=!0,leafOnly:U=!1,checkStrategy:q="all",allowNotLoaded:E=!1}=A;return dt({checkedKeys:st(y),indeterminateKeys:ct(y),keysToUncheck:b==null?[]:It(b),cascade:_,leafOnly:U,checkStrategy:q,allowNotLoaded:E},O)},getNonLeafKeys(b={}){return lo(R,b)}};return O}const Mt=ge({name:"NBaseSelectGroupHeader",props:{clsPrefix:{type:String,required:!0},tmNode:{type:Object,required:!0}},setup(){const{renderLabelRef:e,renderOptionRef:n,labelFieldRef:t,nodePropsRef:o}=bt(mt);return{labelField:t,nodeProps:o,renderLabel:e,renderOption:n}},render(){const{clsPrefix:e,renderLabel:n,renderOption:t,nodeProps:o,tmNode:{rawNode:r}}=this,s=o?.(r),l=n?n(r,!1):ze(r[this.labelField],r,!1),a=v("div",Object.assign({},s,{class:[`${e}-base-select-group-header`,s?.class]}),l);return r.render?r.render({node:a,option:r}):t?t({node:a,option:r,selected:!1}):a}});function zo(e,n){return v(Et,{name:"fade-in-scale-up-transition"},{default:()=>e?v(Tn,{clsPrefix:n,class:`${n}-base-select-option__check`},{default:()=>v(oo)}):null})}const Bt=ge({name:"NBaseSelectOption",props:{clsPrefix:{type:String,required:!0},tmNode:{type:Object,required:!0}},setup(e){const{valueRef:n,pendingTmNodeRef:t,multipleRef:o,valueSetRef:r,renderLabelRef:s,renderOptionRef:l,labelFieldRef:a,valueFieldRef:d,showCheckmarkRef:c,nodePropsRef:p,handleOptionClick:g,handleOptionMouseEnter:R}=bt(mt),I=Fe(()=>{const{value:P}=t;return P?e.tmNode.key===P.key:!1});function m(P){const{tmNode:k}=e;k.disabled||g(P,k)}function w(P){const{tmNode:k}=e;k.disabled||R(P,k)}function F(P){const{tmNode:k}=e,{value:O}=I;k.disabled||O||R(P,k)}return{multiple:o,isGrouped:Fe(()=>{const{tmNode:P}=e,{parent:k}=P;return k&&k.rawNode.type==="group"}),showCheckmark:c,nodeProps:p,isPending:I,isSelected:Fe(()=>{const{value:P}=n,{value:k}=o;if(P===null)return!1;const O=e.tmNode.rawNode[d.value];if(k){const{value:b}=r;return b.has(O)}else return P===O}),labelField:a,renderLabel:s,renderOption:l,handleMouseMove:F,handleMouseEnter:w,handleClick:m}},render(){const{clsPrefix:e,tmNode:{rawNode:n},isSelected:t,isPending:o,isGrouped:r,showCheckmark:s,nodeProps:l,renderOption:a,renderLabel:d,handleClick:c,handleMouseEnter:p,handleMouseMove:g}=this,R=zo(t,e),I=d?[d(n,t),s&&R]:[ze(n[this.labelField],n,t),s&&R],m=l?.(n),w=v("div",Object.assign({},m,{class:[`${e}-base-select-option`,n.class,m?.class,{[`${e}-base-select-option--disabled`]:n.disabled,[`${e}-base-select-option--selected`]:t,[`${e}-base-select-option--grouped`]:r,[`${e}-base-select-option--pending`]:o,[`${e}-base-select-option--show-checkmark`]:s}],style:[m?.style||"",n.style||""],onClick:at([c,m?.onClick]),onMouseenter:at([p,m?.onMouseenter]),onMousemove:at([g,m?.onMousemove])}),v("div",{class:`${e}-base-select-option__content`},I));return n.render?n.render({node:w,option:n,selected:t}):a?a({node:w,option:n,selected:t}):w}}),Mo=H("base-select-menu",`
 line-height: 1.5;
 outline: none;
 z-index: 0;
 position: relative;
 border-radius: var(--n-border-radius);
 transition:
 background-color .3s var(--n-bezier),
 box-shadow .3s var(--n-bezier);
 background-color: var(--n-color);
`,[H("scrollbar",`
 max-height: var(--n-height);
 `),H("virtual-list",`
 max-height: var(--n-height);
 `),H("base-select-option",`
 min-height: var(--n-option-height);
 font-size: var(--n-option-font-size);
 display: flex;
 align-items: center;
 `,[j("content",`
 z-index: 1;
 white-space: nowrap;
 text-overflow: ellipsis;
 overflow: hidden;
 `)]),H("base-select-group-header",`
 min-height: var(--n-option-height);
 font-size: .93em;
 display: flex;
 align-items: center;
 `),H("base-select-menu-option-wrapper",`
 position: relative;
 width: 100%;
 `),j("loading, empty",`
 display: flex;
 padding: 12px 32px;
 flex: 1;
 justify-content: center;
 `),j("loading",`
 color: var(--n-loading-color);
 font-size: var(--n-loading-size);
 `),j("header",`
 padding: 8px var(--n-option-padding-left);
 font-size: var(--n-option-font-size);
 transition: 
 color .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
 border-bottom: 1px solid var(--n-action-divider-color);
 color: var(--n-action-text-color);
 `),j("action",`
 padding: 8px var(--n-option-padding-left);
 font-size: var(--n-option-font-size);
 transition: 
 color .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
 border-top: 1px solid var(--n-action-divider-color);
 color: var(--n-action-text-color);
 `),H("base-select-group-header",`
 position: relative;
 cursor: default;
 padding: var(--n-option-padding);
 color: var(--n-group-header-text-color);
 `),H("base-select-option",`
 cursor: pointer;
 position: relative;
 padding: var(--n-option-padding);
 transition:
 color .3s var(--n-bezier),
 opacity .3s var(--n-bezier);
 box-sizing: border-box;
 color: var(--n-option-text-color);
 opacity: 1;
 `,[te("show-checkmark",`
 padding-right: calc(var(--n-option-padding-right) + 20px);
 `),de("&::before",`
 content: "";
 position: absolute;
 left: 4px;
 right: 4px;
 top: 0;
 bottom: 0;
 border-radius: var(--n-border-radius);
 transition: background-color .3s var(--n-bezier);
 `),de("&:active",`
 color: var(--n-option-text-color-pressed);
 `),te("grouped",`
 padding-left: calc(var(--n-option-padding-left) * 1.5);
 `),te("pending",[de("&::before",`
 background-color: var(--n-option-color-pending);
 `)]),te("selected",`
 color: var(--n-option-text-color-active);
 `,[de("&::before",`
 background-color: var(--n-option-color-active);
 `),te("pending",[de("&::before",`
 background-color: var(--n-option-color-active-pending);
 `)])]),te("disabled",`
 cursor: not-allowed;
 `,[Te("selected",`
 color: var(--n-option-text-color-disabled);
 `),te("selected",`
 opacity: var(--n-option-opacity-disabled);
 `)]),j("check",`
 font-size: 16px;
 position: absolute;
 right: calc(var(--n-option-padding-right) - 4px);
 top: calc(50% - 7px);
 color: var(--n-option-check-color);
 transition: color .3s var(--n-bezier);
 `,[Nt({enterScale:"0.5"})])])]),Bo=ge({name:"InternalSelectMenu",props:Object.assign(Object.assign({},xe.props),{clsPrefix:{type:String,required:!0},scrollable:{type:Boolean,default:!0},treeMate:{type:Object,required:!0},multiple:Boolean,size:{type:String,default:"medium"},value:{type:[String,Number,Array],default:null},autoPending:Boolean,virtualScroll:{type:Boolean,default:!0},show:{type:Boolean,default:!0},labelField:{type:String,default:"label"},valueField:{type:String,default:"value"},loading:Boolean,focusable:Boolean,renderLabel:Function,renderOption:Function,nodeProps:Function,showCheckmark:{type:Boolean,default:!0},onMousedown:Function,onScroll:Function,onFocus:Function,onBlur:Function,onKeyup:Function,onKeydown:Function,onTabOut:Function,onMouseenter:Function,onMouseleave:Function,onResize:Function,resetMenuOnOptionsChange:{type:Boolean,default:!0},inlineThemeDisabled:Boolean,scrollbarProps:Object,onToggle:Function}),setup(e){const{mergedClsPrefixRef:n,mergedRtlRef:t,mergedComponentPropsRef:o}=Qe(e),r=yt("InternalSelectMenu",t,n),s=xe("InternalSelectMenu","-internal-select-menu",Mo,zn,e,le(e,"clsPrefix")),l=M(null),a=M(null),d=M(null),c=K(()=>e.treeMate.getFlattenedNodes()),p=K(()=>po(c.value)),g=M(null);function R(){const{treeMate:u}=e;let x=null;const{value:J}=e;J===null?x=u.getFirstAvailableNode():(e.multiple?x=u.getNode((J||[])[(J||[]).length-1]):x=u.getNode(J),(!x||x.disabled)&&(x=u.getFirstAvailableNode())),B(x||null)}function I(){const{value:u}=g;u&&!e.treeMate.getNode(u.key)&&(g.value=null)}let m;Oe(()=>e.show,u=>{u?m=Oe(()=>e.treeMate,()=>{e.resetMenuOnOptionsChange?(e.autoPending?R():I(),pt(Q)):I()},{immediate:!0}):m?.()},{immediate:!0}),_t(()=>{m?.()});const w=K(()=>ht(s.value.self[oe("optionHeight",e.size)])),F=K(()=>Me(s.value.self[oe("padding",e.size)])),P=K(()=>e.multiple&&Array.isArray(e.value)?new Set(e.value):new Set),k=K(()=>{const u=c.value;return u&&u.length===0}),O=K(()=>{var u,x;return(x=(u=o?.value)===null||u===void 0?void 0:u.Select)===null||x===void 0?void 0:x.renderEmpty});function b(u){const{onToggle:x}=e;x&&x(u)}function y(u){const{onScroll:x}=e;x&&x(u)}function A(u){var x;(x=d.value)===null||x===void 0||x.sync(),y(u)}function _(){var u;(u=d.value)===null||u===void 0||u.sync()}function U(){const{value:u}=g;return u||null}function q(u,x){x.disabled||B(x,!1)}function E(u,x){x.disabled||b(x)}function ee(u){var x;Le(u,"action")||(x=e.onKeyup)===null||x===void 0||x.call(e,u)}function ie(u){var x;Le(u,"action")||(x=e.onKeydown)===null||x===void 0||x.call(e,u)}function h(u){var x;(x=e.onMousedown)===null||x===void 0||x.call(e,u),!e.focusable&&u.preventDefault()}function T(){const{value:u}=g;u&&B(u.getNext({loop:!0}),!0)}function D(){const{value:u}=g;u&&B(u.getPrev({loop:!0}),!0)}function B(u,x=!1){g.value=u,x&&Q()}function Q(){var u,x;const J=g.value;if(!J)return;const fe=p.value(J.key);fe!==null&&(e.virtualScroll?(u=a.value)===null||u===void 0||u.scrollTo({index:fe}):(x=d.value)===null||x===void 0||x.scrollTo({index:fe,elSize:w.value}))}function V(u){var x,J;!((x=l.value)===null||x===void 0)&&x.contains(u.target)&&((J=e.onFocus)===null||J===void 0||J.call(e,u))}function X(u){var x,J;!((x=l.value)===null||x===void 0)&&x.contains(u.relatedTarget)||(J=e.onBlur)===null||J===void 0||J.call(e,u)}Xe(mt,{handleOptionMouseEnter:q,handleOptionClick:E,valueSetRef:P,pendingTmNodeRef:g,nodePropsRef:le(e,"nodeProps"),showCheckmarkRef:le(e,"showCheckmark"),multipleRef:le(e,"multiple"),valueRef:le(e,"value"),renderLabelRef:le(e,"renderLabel"),renderOptionRef:le(e,"renderOption"),labelFieldRef:le(e,"labelField"),valueFieldRef:le(e,"valueField")}),Xe(Mn,l),We(()=>{const{value:u}=d;u&&u.sync()});const G=K(()=>{const{size:u}=e,{common:{cubicBezierEaseInOut:x},self:{height:J,borderRadius:fe,color:he,groupHeaderTextColor:ce,actionDividerColor:Ce,optionTextColorPressed:be,optionTextColor:Se,optionTextColorDisabled:pe,optionTextColorActive:Be,optionOpacityDisabled:_e,optionCheckColor:ke,actionTextColor:Re,optionColorPending:Ee,optionColorActive:Ae,loadingColor:$e,loadingSize:Ie,optionColorActivePending:Pe,[oe("optionFontSize",u)]:ue,[oe("optionHeight",u)]:f,[oe("optionPadding",u)]:S}}=s.value;return{"--n-height":J,"--n-action-divider-color":Ce,"--n-action-text-color":Re,"--n-bezier":x,"--n-border-radius":fe,"--n-color":he,"--n-option-font-size":ue,"--n-group-header-text-color":ce,"--n-option-check-color":ke,"--n-option-color-pending":Ee,"--n-option-color-active":Ae,"--n-option-color-active-pending":Pe,"--n-option-height":f,"--n-option-opacity-disabled":_e,"--n-option-text-color":Se,"--n-option-text-color-active":Be,"--n-option-text-color-disabled":pe,"--n-option-text-color-pressed":be,"--n-option-padding":S,"--n-option-padding-left":Me(S,"left"),"--n-option-padding-right":Me(S,"right"),"--n-loading-color":$e,"--n-loading-size":Ie}}),{inlineThemeDisabled:Y}=e,ae=Y?et("internal-select-menu",K(()=>e.size[0]),G,e):void 0,se={selfRef:l,next:T,prev:D,getPendingTmNode:U};return Kt(l,e.onResize),Object.assign({mergedTheme:s,mergedClsPrefix:n,rtlEnabled:r,virtualListRef:a,scrollbarRef:d,itemSize:w,padding:F,flattenedNodes:c,empty:k,mergedRenderEmpty:O,virtualListContainer(){const{value:u}=a;return u?.listElRef},virtualListContent(){const{value:u}=a;return u?.itemsElRef},doScroll:y,handleFocusin:V,handleFocusout:X,handleKeyUp:ee,handleKeyDown:ie,handleMouseDown:h,handleVirtualListResize:_,handleVirtualListScroll:A,cssVars:Y?void 0:G,themeClass:ae?.themeClass,onRender:ae?.onRender},se)},render(){const{$slots:e,virtualScroll:n,clsPrefix:t,mergedTheme:o,themeClass:r,onRender:s}=this;return s?.(),v("div",{ref:"selfRef",tabindex:this.focusable?0:-1,class:[`${t}-base-select-menu`,this.rtlEnabled&&`${t}-base-select-menu--rtl`,r,this.multiple&&`${t}-base-select-menu--multiple`],style:this.cssVars,onFocusin:this.handleFocusin,onFocusout:this.handleFocusout,onKeyup:this.handleKeyUp,onKeydown:this.handleKeyDown,onMousedown:this.handleMouseDown,onMouseenter:this.onMouseenter,onMouseleave:this.onMouseleave},Ye(e.header,l=>l&&v("div",{class:`${t}-base-select-menu__header`,"data-header":!0,key:"header"},l)),this.loading?v("div",{class:`${t}-base-select-menu__loading`},v(On,{clsPrefix:t,strokeWidth:20})):this.empty?v("div",{class:`${t}-base-select-menu__empty`,"data-empty":!0},In(e.empty,()=>{var l;return[((l=this.mergedRenderEmpty)===null||l===void 0?void 0:l.call(this))||v(Zn,{theme:o.peers.Empty,themeOverrides:o.peerOverrides.Empty,size:this.size})]})):v(Fn,Object.assign({ref:"scrollbarRef",theme:o.peers.Scrollbar,themeOverrides:o.peerOverrides.Scrollbar,scrollable:this.scrollable,container:n?this.virtualListContainer:void 0,content:n?this.virtualListContent:void 0,onScroll:n?void 0:this.doScroll},this.scrollbarProps),{default:()=>n?v(to,{ref:"virtualListRef",class:`${t}-virtual-list`,items:this.flattenedNodes,itemSize:this.itemSize,showScrollbar:!1,paddingTop:this.padding.top,paddingBottom:this.padding.bottom,onResize:this.handleVirtualListResize,onScroll:this.handleVirtualListScroll,itemResizable:!0},{default:({item:l})=>l.isGroup?v(Mt,{key:l.key,clsPrefix:t,tmNode:l}):l.ignored?null:v(Bt,{clsPrefix:t,key:l.key,tmNode:l})}):v("div",{class:`${t}-base-select-menu-option-wrapper`,style:{paddingTop:this.padding.top,paddingBottom:this.padding.bottom}},this.flattenedNodes.map(l=>l.isGroup?v(Mt,{key:l.key,clsPrefix:t,tmNode:l}):v(Bt,{clsPrefix:t,key:l.key,tmNode:l})))}),Ye(e.action,l=>l&&[v("div",{class:`${t}-base-select-menu__action`,"data-action":!0,key:"action"},l),v(ro,{onFocus:this.onTabOut,key:"focus-detector"})]))}});function _o(e){const{textColor2:n,primaryColorHover:t,primaryColorPressed:o,primaryColor:r,infoColor:s,successColor:l,warningColor:a,errorColor:d,baseColor:c,borderColor:p,opacityDisabled:g,tagColor:R,closeIconColor:I,closeIconColorHover:m,closeIconColorPressed:w,borderRadiusSmall:F,fontSizeMini:P,fontSizeTiny:k,fontSizeSmall:O,fontSizeMedium:b,heightMini:y,heightTiny:A,heightSmall:_,heightMedium:U,closeColorHover:q,closeColorPressed:E,buttonColor2Hover:ee,buttonColor2Pressed:ie,fontWeightStrong:h}=e;return Object.assign(Object.assign({},_n),{closeBorderRadius:F,heightTiny:y,heightSmall:A,heightMedium:_,heightLarge:U,borderRadius:F,opacityDisabled:g,fontSizeTiny:P,fontSizeSmall:k,fontSizeMedium:O,fontSizeLarge:b,fontWeightStrong:h,textColorCheckable:n,textColorHoverCheckable:n,textColorPressedCheckable:n,textColorChecked:c,colorCheckable:"#0000",colorHoverCheckable:ee,colorPressedCheckable:ie,colorChecked:r,colorCheckedHover:t,colorCheckedPressed:o,border:`1px solid ${p}`,textColor:n,color:R,colorBordered:"rgb(250, 250, 252)",closeIconColor:I,closeIconColorHover:m,closeIconColorPressed:w,closeColorHover:q,closeColorPressed:E,borderPrimary:`1px solid ${Z(r,{alpha:.3})}`,textColorPrimary:r,colorPrimary:Z(r,{alpha:.12}),colorBorderedPrimary:Z(r,{alpha:.1}),closeIconColorPrimary:r,closeIconColorHoverPrimary:r,closeIconColorPressedPrimary:r,closeColorHoverPrimary:Z(r,{alpha:.12}),closeColorPressedPrimary:Z(r,{alpha:.18}),borderInfo:`1px solid ${Z(s,{alpha:.3})}`,textColorInfo:s,colorInfo:Z(s,{alpha:.12}),colorBorderedInfo:Z(s,{alpha:.1}),closeIconColorInfo:s,closeIconColorHoverInfo:s,closeIconColorPressedInfo:s,closeColorHoverInfo:Z(s,{alpha:.12}),closeColorPressedInfo:Z(s,{alpha:.18}),borderSuccess:`1px solid ${Z(l,{alpha:.3})}`,textColorSuccess:l,colorSuccess:Z(l,{alpha:.12}),colorBorderedSuccess:Z(l,{alpha:.1}),closeIconColorSuccess:l,closeIconColorHoverSuccess:l,closeIconColorPressedSuccess:l,closeColorHoverSuccess:Z(l,{alpha:.12}),closeColorPressedSuccess:Z(l,{alpha:.18}),borderWarning:`1px solid ${Z(a,{alpha:.35})}`,textColorWarning:a,colorWarning:Z(a,{alpha:.15}),colorBorderedWarning:Z(a,{alpha:.12}),closeIconColorWarning:a,closeIconColorHoverWarning:a,closeIconColorPressedWarning:a,closeColorHoverWarning:Z(a,{alpha:.12}),closeColorPressedWarning:Z(a,{alpha:.18}),borderError:`1px solid ${Z(d,{alpha:.23})}`,textColorError:d,colorError:Z(d,{alpha:.1}),colorBorderedError:Z(d,{alpha:.08}),closeIconColorError:d,closeIconColorHoverError:d,closeIconColorPressedError:d,closeColorHoverError:Z(d,{alpha:.12}),closeColorPressedError:Z(d,{alpha:.18})})}const Eo={common:Bn,self:_o},Ao={color:Object,type:{type:String,default:"default"},round:Boolean,size:{type:String,default:"medium"},closable:Boolean,disabled:{type:Boolean,default:void 0}},$o=H("tag",`
 --n-close-margin: var(--n-close-margin-top) var(--n-close-margin-right) var(--n-close-margin-bottom) var(--n-close-margin-left);
 white-space: nowrap;
 position: relative;
 box-sizing: border-box;
 cursor: default;
 display: inline-flex;
 align-items: center;
 flex-wrap: nowrap;
 padding: var(--n-padding);
 border-radius: var(--n-border-radius);
 color: var(--n-text-color);
 background-color: var(--n-color);
 transition: 
 border-color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 color .3s var(--n-bezier),
 box-shadow .3s var(--n-bezier),
 opacity .3s var(--n-bezier);
 line-height: 1;
 height: var(--n-height);
 font-size: var(--n-font-size);
`,[te("strong",`
 font-weight: var(--n-font-weight-strong);
 `),j("border",`
 pointer-events: none;
 position: absolute;
 left: 0;
 right: 0;
 top: 0;
 bottom: 0;
 border-radius: inherit;
 border: var(--n-border);
 transition: border-color .3s var(--n-bezier);
 `),j("icon",`
 display: flex;
 margin: 0 4px 0 0;
 color: var(--n-text-color);
 transition: color .3s var(--n-bezier);
 font-size: var(--n-avatar-size-override);
 `),j("avatar",`
 display: flex;
 margin: 0 6px 0 0;
 `),j("close",`
 margin: var(--n-close-margin);
 transition:
 background-color .3s var(--n-bezier),
 color .3s var(--n-bezier);
 `),te("round",`
 padding: 0 calc(var(--n-height) / 3);
 border-radius: calc(var(--n-height) / 2);
 `,[j("icon",`
 margin: 0 4px 0 calc((var(--n-height) - 8px) / -2);
 `),j("avatar",`
 margin: 0 6px 0 calc((var(--n-height) - 8px) / -2);
 `),te("closable",`
 padding: 0 calc(var(--n-height) / 4) 0 calc(var(--n-height) / 3);
 `)]),te("icon, avatar",[te("round",`
 padding: 0 calc(var(--n-height) / 3) 0 calc(var(--n-height) / 2);
 `)]),te("disabled",`
 cursor: not-allowed !important;
 opacity: var(--n-opacity-disabled);
 `),te("checkable",`
 cursor: pointer;
 box-shadow: none;
 color: var(--n-text-color-checkable);
 background-color: var(--n-color-checkable);
 `,[Te("disabled",[de("&:hover","background-color: var(--n-color-hover-checkable);",[Te("checked","color: var(--n-text-color-hover-checkable);")]),de("&:active","background-color: var(--n-color-pressed-checkable);",[Te("checked","color: var(--n-text-color-pressed-checkable);")])]),te("checked",`
 color: var(--n-text-color-checked);
 background-color: var(--n-color-checked);
 `,[Te("disabled",[de("&:hover","background-color: var(--n-color-checked-hover);"),de("&:active","background-color: var(--n-color-checked-pressed);")])])])]),No=Object.assign(Object.assign(Object.assign({},xe.props),Ao),{bordered:{type:Boolean,default:void 0},checked:Boolean,checkable:Boolean,strong:Boolean,triggerClickOnClose:Boolean,onClose:[Array,Function],onMouseenter:Function,onMouseleave:Function,"onUpdate:checked":Function,onUpdateChecked:Function,internalCloseFocusable:{type:Boolean,default:!0},internalCloseIsButtonTag:{type:Boolean,default:!0},onCheckedChange:Function}),Lo=An("n-tag"),ut=ge({name:"Tag",props:No,slots:Object,setup(e){const n=M(null),{mergedBorderedRef:t,mergedClsPrefixRef:o,inlineThemeDisabled:r,mergedRtlRef:s,mergedComponentPropsRef:l}=Qe(e),a=K(()=>{var w,F;return e.size||((F=(w=l?.value)===null||w===void 0?void 0:w.Tag)===null||F===void 0?void 0:F.size)||"medium"}),d=xe("Tag","-tag",$o,Eo,e,o);Xe(Lo,{roundRef:le(e,"round")});function c(){if(!e.disabled&&e.checkable){const{checked:w,onCheckedChange:F,onUpdateChecked:P,"onUpdate:checked":k}=e;P&&P(!w),k&&k(!w),F&&F(!w)}}function p(w){if(e.triggerClickOnClose||w.stopPropagation(),!e.disabled){const{onClose:F}=e;F&&ve(F,w)}}const g={setTextContent(w){const{value:F}=n;F&&(F.textContent=w)}},R=yt("Tag",s,o),I=K(()=>{const{type:w,color:{color:F,textColor:P}={}}=e,k=a.value,{common:{cubicBezierEaseInOut:O},self:{padding:b,closeMargin:y,borderRadius:A,opacityDisabled:_,textColorCheckable:U,textColorHoverCheckable:q,textColorPressedCheckable:E,textColorChecked:ee,colorCheckable:ie,colorHoverCheckable:h,colorPressedCheckable:T,colorChecked:D,colorCheckedHover:B,colorCheckedPressed:Q,closeBorderRadius:V,fontWeightStrong:X,[oe("colorBordered",w)]:G,[oe("closeSize",k)]:Y,[oe("closeIconSize",k)]:ae,[oe("fontSize",k)]:se,[oe("height",k)]:u,[oe("color",w)]:x,[oe("textColor",w)]:J,[oe("border",w)]:fe,[oe("closeIconColor",w)]:he,[oe("closeIconColorHover",w)]:ce,[oe("closeIconColorPressed",w)]:Ce,[oe("closeColorHover",w)]:be,[oe("closeColorPressed",w)]:Se}}=d.value,pe=Me(y);return{"--n-font-weight-strong":X,"--n-avatar-size-override":`calc(${u} - 8px)`,"--n-bezier":O,"--n-border-radius":A,"--n-border":fe,"--n-close-icon-size":ae,"--n-close-color-pressed":Se,"--n-close-color-hover":be,"--n-close-border-radius":V,"--n-close-icon-color":he,"--n-close-icon-color-hover":ce,"--n-close-icon-color-pressed":Ce,"--n-close-icon-color-disabled":he,"--n-close-margin-top":pe.top,"--n-close-margin-right":pe.right,"--n-close-margin-bottom":pe.bottom,"--n-close-margin-left":pe.left,"--n-close-size":Y,"--n-color":F||(t.value?G:x),"--n-color-checkable":ie,"--n-color-checked":D,"--n-color-checked-hover":B,"--n-color-checked-pressed":Q,"--n-color-hover-checkable":h,"--n-color-pressed-checkable":T,"--n-font-size":se,"--n-height":u,"--n-opacity-disabled":_,"--n-padding":b,"--n-text-color":P||J,"--n-text-color-checkable":U,"--n-text-color-checked":ee,"--n-text-color-hover-checkable":q,"--n-text-color-pressed-checkable":E}}),m=r?et("tag",K(()=>{let w="";const{type:F,color:{color:P,textColor:k}={}}=e;return w+=F[0],w+=a.value[0],P&&(w+=`a${xt(P)}`),k&&(w+=`b${xt(k)}`),t.value&&(w+="c"),w}),I,e):void 0;return Object.assign(Object.assign({},g),{rtlEnabled:R,mergedClsPrefix:o,contentRef:n,mergedBordered:t,handleClick:c,handleCloseClick:p,cssVars:r?void 0:I,themeClass:m?.themeClass,onRender:m?.onRender})},render(){var e,n;const{mergedClsPrefix:t,rtlEnabled:o,closable:r,color:{borderColor:s}={},round:l,onRender:a,$slots:d}=this;a?.();const c=Ye(d.avatar,g=>g&&v("div",{class:`${t}-tag__avatar`},g)),p=Ye(d.icon,g=>g&&v("div",{class:`${t}-tag__icon`},g));return v("div",{class:[`${t}-tag`,this.themeClass,{[`${t}-tag--rtl`]:o,[`${t}-tag--strong`]:this.strong,[`${t}-tag--disabled`]:this.disabled,[`${t}-tag--checkable`]:this.checkable,[`${t}-tag--checked`]:this.checkable&&this.checked,[`${t}-tag--round`]:l,[`${t}-tag--avatar`]:c,[`${t}-tag--icon`]:p,[`${t}-tag--closable`]:r}],style:this.cssVars,onClick:this.handleClick,onMouseenter:this.onMouseenter,onMouseleave:this.onMouseleave},p||c,v("span",{class:`${t}-tag__content`,ref:"contentRef"},(n=(e=this.$slots).default)===null||n===void 0?void 0:n.call(e)),!this.checkable&&r?v(En,{clsPrefix:t,class:`${t}-tag__close`,disabled:this.disabled,onClick:this.handleCloseClick,focusable:this.internalCloseFocusable,round:l,isButtonTag:this.internalCloseIsButtonTag,absolute:!0}):null,!this.checkable&&this.mergedBordered?v("div",{class:`${t}-tag__border`,style:{borderColor:s}}):null)}}),Wo=de([H("base-selection",`
 --n-padding-single: var(--n-padding-single-top) var(--n-padding-single-right) var(--n-padding-single-bottom) var(--n-padding-single-left);
 --n-padding-multiple: var(--n-padding-multiple-top) var(--n-padding-multiple-right) var(--n-padding-multiple-bottom) var(--n-padding-multiple-left);
 position: relative;
 z-index: auto;
 box-shadow: none;
 width: 100%;
 max-width: 100%;
 display: inline-block;
 vertical-align: bottom;
 border-radius: var(--n-border-radius);
 min-height: var(--n-height);
 line-height: 1.5;
 font-size: var(--n-font-size);
 `,[H("base-loading",`
 color: var(--n-loading-color);
 `),H("base-selection-tags","min-height: var(--n-height);"),j("border, state-border",`
 position: absolute;
 left: 0;
 right: 0;
 top: 0;
 bottom: 0;
 pointer-events: none;
 border: var(--n-border);
 border-radius: inherit;
 transition:
 box-shadow .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
 `),j("state-border",`
 z-index: 1;
 border-color: #0000;
 `),H("base-suffix",`
 cursor: pointer;
 position: absolute;
 top: 50%;
 transform: translateY(-50%);
 right: 10px;
 `,[j("arrow",`
 font-size: var(--n-arrow-size);
 color: var(--n-arrow-color);
 transition: color .3s var(--n-bezier);
 `)]),H("base-selection-overlay",`
 display: flex;
 align-items: center;
 white-space: nowrap;
 pointer-events: none;
 position: absolute;
 top: 0;
 right: 0;
 bottom: 0;
 left: 0;
 padding: var(--n-padding-single);
 transition: color .3s var(--n-bezier);
 `,[j("wrapper",`
 flex-basis: 0;
 flex-grow: 1;
 overflow: hidden;
 text-overflow: ellipsis;
 `)]),H("base-selection-placeholder",`
 color: var(--n-placeholder-color);
 `,[j("inner",`
 max-width: 100%;
 overflow: hidden;
 `)]),H("base-selection-tags",`
 cursor: pointer;
 outline: none;
 box-sizing: border-box;
 position: relative;
 z-index: auto;
 display: flex;
 padding: var(--n-padding-multiple);
 flex-wrap: wrap;
 align-items: center;
 width: 100%;
 vertical-align: bottom;
 background-color: var(--n-color);
 border-radius: inherit;
 transition:
 color .3s var(--n-bezier),
 box-shadow .3s var(--n-bezier),
 background-color .3s var(--n-bezier);
 `),H("base-selection-label",`
 height: var(--n-height);
 display: inline-flex;
 width: 100%;
 vertical-align: bottom;
 cursor: pointer;
 outline: none;
 z-index: auto;
 box-sizing: border-box;
 position: relative;
 transition:
 color .3s var(--n-bezier),
 box-shadow .3s var(--n-bezier),
 background-color .3s var(--n-bezier);
 border-radius: inherit;
 background-color: var(--n-color);
 align-items: center;
 `,[H("base-selection-input",`
 font-size: inherit;
 line-height: inherit;
 outline: none;
 cursor: pointer;
 box-sizing: border-box;
 border:none;
 width: 100%;
 padding: var(--n-padding-single);
 background-color: #0000;
 color: var(--n-text-color);
 transition: color .3s var(--n-bezier);
 caret-color: var(--n-caret-color);
 `,[j("content",`
 text-overflow: ellipsis;
 overflow: hidden;
 white-space: nowrap; 
 `)]),j("render-label",`
 color: var(--n-text-color);
 `)]),Te("disabled",[de("&:hover",[j("state-border",`
 box-shadow: var(--n-box-shadow-hover);
 border: var(--n-border-hover);
 `)]),te("focus",[j("state-border",`
 box-shadow: var(--n-box-shadow-focus);
 border: var(--n-border-focus);
 `)]),te("active",[j("state-border",`
 box-shadow: var(--n-box-shadow-active);
 border: var(--n-border-active);
 `),H("base-selection-label","background-color: var(--n-color-active);"),H("base-selection-tags","background-color: var(--n-color-active);")])]),te("disabled","cursor: not-allowed;",[j("arrow",`
 color: var(--n-arrow-color-disabled);
 `),H("base-selection-label",`
 cursor: not-allowed;
 background-color: var(--n-color-disabled);
 `,[H("base-selection-input",`
 cursor: not-allowed;
 color: var(--n-text-color-disabled);
 `),j("render-label",`
 color: var(--n-text-color-disabled);
 `)]),H("base-selection-tags",`
 cursor: not-allowed;
 background-color: var(--n-color-disabled);
 `),H("base-selection-placeholder",`
 cursor: not-allowed;
 color: var(--n-placeholder-color-disabled);
 `)]),H("base-selection-input-tag",`
 height: calc(var(--n-height) - 6px);
 line-height: calc(var(--n-height) - 6px);
 outline: none;
 display: none;
 position: relative;
 margin-bottom: 3px;
 max-width: 100%;
 vertical-align: bottom;
 `,[j("input",`
 font-size: inherit;
 font-family: inherit;
 min-width: 1px;
 padding: 0;
 background-color: #0000;
 outline: none;
 border: none;
 max-width: 100%;
 overflow: hidden;
 width: 1em;
 line-height: inherit;
 cursor: pointer;
 color: var(--n-text-color);
 caret-color: var(--n-caret-color);
 `),j("mirror",`
 position: absolute;
 left: 0;
 top: 0;
 white-space: pre;
 visibility: hidden;
 user-select: none;
 -webkit-user-select: none;
 opacity: 0;
 `)]),["warning","error"].map(e=>te(`${e}-status`,[j("state-border",`border: var(--n-border-${e});`),Te("disabled",[de("&:hover",[j("state-border",`
 box-shadow: var(--n-box-shadow-hover-${e});
 border: var(--n-border-hover-${e});
 `)]),te("active",[j("state-border",`
 box-shadow: var(--n-box-shadow-active-${e});
 border: var(--n-border-active-${e});
 `),H("base-selection-label",`background-color: var(--n-color-active-${e});`),H("base-selection-tags",`background-color: var(--n-color-active-${e});`)]),te("focus",[j("state-border",`
 box-shadow: var(--n-box-shadow-focus-${e});
 border: var(--n-border-focus-${e});
 `)])])]))]),H("base-selection-popover",`
 margin-bottom: -3px;
 display: flex;
 flex-wrap: wrap;
 margin-right: -8px;
 `),H("base-selection-tag-wrapper",`
 max-width: 100%;
 display: inline-flex;
 padding: 0 7px 3px 0;
 `,[de("&:last-child","padding-right: 0;"),H("tag",`
 font-size: 14px;
 max-width: 100%;
 `,[j("content",`
 line-height: 1.25;
 text-overflow: ellipsis;
 overflow: hidden;
 `)])])]),Ko=ge({name:"InternalSelection",props:Object.assign(Object.assign({},xe.props),{clsPrefix:{type:String,required:!0},bordered:{type:Boolean,default:void 0},active:Boolean,pattern:{type:String,default:""},placeholder:String,selectedOption:{type:Object,default:null},selectedOptions:{type:Array,default:null},labelField:{type:String,default:"label"},valueField:{type:String,default:"value"},multiple:Boolean,filterable:Boolean,clearable:Boolean,disabled:Boolean,size:{type:String,default:"medium"},loading:Boolean,autofocus:Boolean,showArrow:{type:Boolean,default:!0},inputProps:Object,focused:Boolean,renderTag:Function,onKeydown:Function,onClick:Function,onBlur:Function,onFocus:Function,onDeleteOption:Function,maxTagCount:[String,Number],ellipsisTagPopoverProps:Object,onClear:Function,onPatternInput:Function,onPatternFocus:Function,onPatternBlur:Function,renderLabel:Function,status:String,inlineThemeDisabled:Boolean,ignoreComposition:{type:Boolean,default:!0},onResize:Function}),setup(e){const{mergedClsPrefixRef:n,mergedRtlRef:t}=Qe(e),o=yt("InternalSelection",t,n),r=M(null),s=M(null),l=M(null),a=M(null),d=M(null),c=M(null),p=M(null),g=M(null),R=M(null),I=M(null),m=M(!1),w=M(!1),F=M(!1),P=xe("InternalSelection","-internal-selection",Wo,Wn,e,le(e,"clsPrefix")),k=K(()=>e.clearable&&!e.disabled&&(F.value||e.active)),O=K(()=>e.selectedOption?e.renderTag?e.renderTag({option:e.selectedOption,handleClose:()=>{}}):e.renderLabel?e.renderLabel(e.selectedOption,!0):ze(e.selectedOption[e.labelField],e.selectedOption,!0):e.placeholder),b=K(()=>{const f=e.selectedOption;if(f)return f[e.labelField]}),y=K(()=>e.multiple?!!(Array.isArray(e.selectedOptions)&&e.selectedOptions.length):e.selectedOption!==null);function A(){var f;const{value:S}=r;if(S){const{value:ne}=s;ne&&(ne.style.width=`${S.offsetWidth}px`,e.maxTagCount!=="responsive"&&((f=R.value)===null||f===void 0||f.sync({showAllItemsBeforeCalculate:!1})))}}function _(){const{value:f}=I;f&&(f.style.display="none")}function U(){const{value:f}=I;f&&(f.style.display="inline-block")}Oe(le(e,"active"),f=>{f||_()}),Oe(le(e,"pattern"),()=>{e.multiple&&pt(A)});function q(f){const{onFocus:S}=e;S&&S(f)}function E(f){const{onBlur:S}=e;S&&S(f)}function ee(f){const{onDeleteOption:S}=e;S&&S(f)}function ie(f){const{onClear:S}=e;S&&S(f)}function h(f){const{onPatternInput:S}=e;S&&S(f)}function T(f){var S;(!f.relatedTarget||!(!((S=l.value)===null||S===void 0)&&S.contains(f.relatedTarget)))&&q(f)}function D(f){var S;!((S=l.value)===null||S===void 0)&&S.contains(f.relatedTarget)||E(f)}function B(f){ie(f)}function Q(){F.value=!0}function V(){F.value=!1}function X(f){!e.active||!e.filterable||f.target!==s.value&&f.preventDefault()}function G(f){ee(f)}const Y=M(!1);function ae(f){if(f.key==="Backspace"&&!Y.value&&!e.pattern.length){const{selectedOptions:S}=e;S?.length&&G(S[S.length-1])}}let se=null;function u(f){const{value:S}=r;if(S){const ne=f.target.value;S.textContent=ne,A()}e.ignoreComposition&&Y.value?se=f:h(f)}function x(){Y.value=!0}function J(){Y.value=!1,e.ignoreComposition&&h(se),se=null}function fe(f){var S;w.value=!0,(S=e.onPatternFocus)===null||S===void 0||S.call(e,f)}function he(f){var S;w.value=!1,(S=e.onPatternBlur)===null||S===void 0||S.call(e,f)}function ce(){var f,S;if(e.filterable)w.value=!1,(f=c.value)===null||f===void 0||f.blur(),(S=s.value)===null||S===void 0||S.blur();else if(e.multiple){const{value:ne}=a;ne?.blur()}else{const{value:ne}=d;ne?.blur()}}function Ce(){var f,S,ne;e.filterable?(w.value=!1,(f=c.value)===null||f===void 0||f.focus()):e.multiple?(S=a.value)===null||S===void 0||S.focus():(ne=d.value)===null||ne===void 0||ne.focus()}function be(){const{value:f}=s;f&&(U(),f.focus())}function Se(){const{value:f}=s;f&&f.blur()}function pe(f){const{value:S}=p;S&&S.setTextContent(`+${f}`)}function Be(){const{value:f}=g;return f}function _e(){return s.value}let ke=null;function Re(){ke!==null&&window.clearTimeout(ke)}function Ee(){e.active||(Re(),ke=window.setTimeout(()=>{y.value&&(m.value=!0)},100))}function Ae(){Re()}function $e(f){f||(Re(),m.value=!1)}Oe(y,f=>{f||(m.value=!1)}),We(()=>{Sn(()=>{const f=c.value;f&&(e.disabled?f.removeAttribute("tabindex"):f.tabIndex=w.value?-1:0)})}),Kt(l,e.onResize);const{inlineThemeDisabled:Ie}=e,Pe=K(()=>{const{size:f}=e,{common:{cubicBezierEaseInOut:S},self:{fontWeight:ne,borderRadius:tt,color:nt,placeholderColor:Ke,textColor:De,paddingSingle:He,paddingMultiple:ot,caretColor:rt,colorDisabled:je,textColorDisabled:we,placeholderColorDisabled:i,colorActive:C,boxShadowFocus:z,boxShadowActive:L,boxShadowHover:$,border:N,borderFocus:W,borderHover:re,borderActive:me,arrowColor:Vt,arrowColorDisabled:Ut,loadingColor:Gt,colorActiveWarning:qt,boxShadowFocusWarning:Xt,boxShadowActiveWarning:Yt,boxShadowHoverWarning:Zt,borderWarning:Jt,borderFocusWarning:Qt,borderHoverWarning:en,borderActiveWarning:tn,colorActiveError:nn,boxShadowFocusError:on,boxShadowActiveError:rn,boxShadowHoverError:ln,borderError:an,borderFocusError:sn,borderHoverError:cn,borderActiveError:dn,clearColor:un,clearColorHover:fn,clearColorPressed:hn,clearSize:vn,arrowSize:gn,[oe("height",f)]:bn,[oe("fontSize",f)]:pn}}=P.value,Ve=Me(He),Ue=Me(ot);return{"--n-bezier":S,"--n-border":N,"--n-border-active":me,"--n-border-focus":W,"--n-border-hover":re,"--n-border-radius":tt,"--n-box-shadow-active":L,"--n-box-shadow-focus":z,"--n-box-shadow-hover":$,"--n-caret-color":rt,"--n-color":nt,"--n-color-active":C,"--n-color-disabled":je,"--n-font-size":pn,"--n-height":bn,"--n-padding-single-top":Ve.top,"--n-padding-multiple-top":Ue.top,"--n-padding-single-right":Ve.right,"--n-padding-multiple-right":Ue.right,"--n-padding-single-left":Ve.left,"--n-padding-multiple-left":Ue.left,"--n-padding-single-bottom":Ve.bottom,"--n-padding-multiple-bottom":Ue.bottom,"--n-placeholder-color":Ke,"--n-placeholder-color-disabled":i,"--n-text-color":De,"--n-text-color-disabled":we,"--n-arrow-color":Vt,"--n-arrow-color-disabled":Ut,"--n-loading-color":Gt,"--n-color-active-warning":qt,"--n-box-shadow-focus-warning":Xt,"--n-box-shadow-active-warning":Yt,"--n-box-shadow-hover-warning":Zt,"--n-border-warning":Jt,"--n-border-focus-warning":Qt,"--n-border-hover-warning":en,"--n-border-active-warning":tn,"--n-color-active-error":nn,"--n-box-shadow-focus-error":on,"--n-box-shadow-active-error":rn,"--n-box-shadow-hover-error":ln,"--n-border-error":an,"--n-border-focus-error":sn,"--n-border-hover-error":cn,"--n-border-active-error":dn,"--n-clear-size":vn,"--n-clear-color":un,"--n-clear-color-hover":fn,"--n-clear-color-pressed":hn,"--n-arrow-size":gn,"--n-font-weight":ne}}),ue=Ie?et("internal-selection",K(()=>e.size[0]),Pe,e):void 0;return{mergedTheme:P,mergedClearable:k,mergedClsPrefix:n,rtlEnabled:o,patternInputFocused:w,filterablePlaceholder:O,label:b,selected:y,showTagsPanel:m,isComposing:Y,counterRef:p,counterWrapperRef:g,patternInputMirrorRef:r,patternInputRef:s,selfRef:l,multipleElRef:a,singleElRef:d,patternInputWrapperRef:c,overflowRef:R,inputTagElRef:I,handleMouseDown:X,handleFocusin:T,handleClear:B,handleMouseEnter:Q,handleMouseLeave:V,handleDeleteOption:G,handlePatternKeyDown:ae,handlePatternInputInput:u,handlePatternInputBlur:he,handlePatternInputFocus:fe,handleMouseEnterCounter:Ee,handleMouseLeaveCounter:Ae,handleFocusout:D,handleCompositionEnd:J,handleCompositionStart:x,onPopoverUpdateShow:$e,focus:Ce,focusInput:be,blur:ce,blurInput:Se,updateCounter:pe,getCounter:Be,getTail:_e,renderLabel:e.renderLabel,cssVars:Ie?void 0:Pe,themeClass:ue?.themeClass,onRender:ue?.onRender}},render(){const{status:e,multiple:n,size:t,disabled:o,filterable:r,maxTagCount:s,bordered:l,clsPrefix:a,ellipsisTagPopoverProps:d,onRender:c,renderTag:p,renderLabel:g}=this;c?.();const R=s==="responsive",I=typeof s=="number",m=R||I,w=v($n,null,{default:()=>v(Nn,{clsPrefix:a,loading:this.loading,showArrow:this.showArrow,showClear:this.mergedClearable&&this.selected,onClear:this.handleClear},{default:()=>{var P,k;return(k=(P=this.$slots).arrow)===null||k===void 0?void 0:k.call(P)}})});let F;if(n){const{labelField:P}=this,k=h=>v("div",{class:`${a}-base-selection-tag-wrapper`,key:h.value},p?p({option:h,handleClose:()=>{this.handleDeleteOption(h)}}):v(ut,{size:t,closable:!h.disabled,disabled:o,onClose:()=>{this.handleDeleteOption(h)},internalCloseIsButtonTag:!1,internalCloseFocusable:!1},{default:()=>g?g(h,!0):ze(h[P],h,!0)})),O=()=>(I?this.selectedOptions.slice(0,s):this.selectedOptions).map(k),b=r?v("div",{class:`${a}-base-selection-input-tag`,ref:"inputTagElRef",key:"__input-tag__"},v("input",Object.assign({},this.inputProps,{ref:"patternInputRef",tabindex:-1,disabled:o,value:this.pattern,autofocus:this.autofocus,class:`${a}-base-selection-input-tag__input`,onBlur:this.handlePatternInputBlur,onFocus:this.handlePatternInputFocus,onKeydown:this.handlePatternKeyDown,onInput:this.handlePatternInputInput,onCompositionstart:this.handleCompositionStart,onCompositionend:this.handleCompositionEnd})),v("span",{ref:"patternInputMirrorRef",class:`${a}-base-selection-input-tag__mirror`},this.pattern)):null,y=R?()=>v("div",{class:`${a}-base-selection-tag-wrapper`,ref:"counterWrapperRef"},v(ut,{size:t,ref:"counterRef",onMouseenter:this.handleMouseEnterCounter,onMouseleave:this.handleMouseLeaveCounter,disabled:o})):void 0;let A;if(I){const h=this.selectedOptions.length-s;h>0&&(A=v("div",{class:`${a}-base-selection-tag-wrapper`,key:"__counter__"},v(ut,{size:t,ref:"counterRef",onMouseenter:this.handleMouseEnterCounter,disabled:o},{default:()=>`+${h}`})))}const _=R?r?v(Ot,{ref:"overflowRef",updateCounter:this.updateCounter,getCounter:this.getCounter,getTail:this.getTail,style:{width:"100%",display:"flex",overflow:"hidden"}},{default:O,counter:y,tail:()=>b}):v(Ot,{ref:"overflowRef",updateCounter:this.updateCounter,getCounter:this.getCounter,style:{width:"100%",display:"flex",overflow:"hidden"}},{default:O,counter:y}):I&&A?O().concat(A):O(),U=m?()=>v("div",{class:`${a}-base-selection-popover`},R?O():this.selectedOptions.map(k)):void 0,q=m?Object.assign({show:this.showTagsPanel,trigger:"hover",overlap:!0,placement:"top",width:"trigger",onUpdateShow:this.onPopoverUpdateShow,theme:this.mergedTheme.peers.Popover,themeOverrides:this.mergedTheme.peerOverrides.Popover},d):null,ee=(this.selected?!1:this.active?!this.pattern&&!this.isComposing:!0)?v("div",{class:`${a}-base-selection-placeholder ${a}-base-selection-overlay`},v("div",{class:`${a}-base-selection-placeholder__inner`},this.placeholder)):null,ie=r?v("div",{ref:"patternInputWrapperRef",class:`${a}-base-selection-tags`},_,R?null:b,w):v("div",{ref:"multipleElRef",class:`${a}-base-selection-tags`,tabindex:o?void 0:0},_,w);F=v(xn,null,m?v(Ln,Object.assign({},q,{scrollable:!0,style:"max-height: calc(var(--v-target-height) * 6.6);"}),{trigger:()=>ie,default:U}):ie,ee)}else if(r){const P=this.pattern||this.isComposing,k=this.active?!P:!this.selected,O=this.active?!1:this.selected;F=v("div",{ref:"patternInputWrapperRef",class:`${a}-base-selection-label`,title:this.patternInputFocused?void 0:Ft(this.label)},v("input",Object.assign({},this.inputProps,{ref:"patternInputRef",class:`${a}-base-selection-input`,value:this.active?this.pattern:"",placeholder:"",readonly:o,disabled:o,tabindex:-1,autofocus:this.autofocus,onFocus:this.handlePatternInputFocus,onBlur:this.handlePatternInputBlur,onInput:this.handlePatternInputInput,onCompositionstart:this.handleCompositionStart,onCompositionend:this.handleCompositionEnd})),O?v("div",{class:`${a}-base-selection-label__render-label ${a}-base-selection-overlay`,key:"input"},v("div",{class:`${a}-base-selection-overlay__wrapper`},p?p({option:this.selectedOption,handleClose:()=>{}}):g?g(this.selectedOption,!0):ze(this.label,this.selectedOption,!0))):null,k?v("div",{class:`${a}-base-selection-placeholder ${a}-base-selection-overlay`,key:"placeholder"},v("div",{class:`${a}-base-selection-overlay__wrapper`},this.filterablePlaceholder)):null,w)}else F=v("div",{ref:"singleElRef",class:`${a}-base-selection-label`,tabindex:this.disabled?void 0:0},this.label!==void 0?v("div",{class:`${a}-base-selection-input`,title:Ft(this.label),key:"input"},v("div",{class:`${a}-base-selection-input__content`},p?p({option:this.selectedOption,handleClose:()=>{}}):g?g(this.selectedOption,!0):ze(this.label,this.selectedOption,!0))):v("div",{class:`${a}-base-selection-placeholder ${a}-base-selection-overlay`,key:"placeholder"},v("div",{class:`${a}-base-selection-placeholder__inner`},this.placeholder)),w);return v("div",{ref:"selfRef",class:[`${a}-base-selection`,this.rtlEnabled&&`${a}-base-selection--rtl`,this.themeClass,e&&`${a}-base-selection--${e}-status`,{[`${a}-base-selection--active`]:this.active,[`${a}-base-selection--selected`]:this.selected||this.active&&this.pattern,[`${a}-base-selection--disabled`]:this.disabled,[`${a}-base-selection--multiple`]:this.multiple,[`${a}-base-selection--focus`]:this.focused}],style:this.cssVars,onClick:this.onClick,onMouseenter:this.handleMouseEnter,onMouseleave:this.handleMouseLeave,onKeydown:this.onKeydown,onFocusin:this.handleFocusin,onFocusout:this.handleFocusout,onMousedown:this.handleMouseDown},F,l?v("div",{class:`${a}-base-selection__border`}):null,l?v("div",{class:`${a}-base-selection__state-border`}):null)}});function Je(e){return e.type==="group"}function jt(e){return e.type==="ignored"}function ft(e,n){try{return!!(1+n.toString().toLowerCase().indexOf(e.trim().toLowerCase()))}catch{return!1}}function Do(e,n){return{getIsGroup:Je,getIgnored:jt,getKey(o){return Je(o)?o.name||o.key||"key-required":o[e]},getChildren(o){return o[n]}}}function Ho(e,n,t,o){if(!n)return e;function r(s){if(!Array.isArray(s))return[];const l=[];for(const a of s)if(Je(a)){const d=r(a[o]);d.length&&l.push(Object.assign({},a,{[o]:d}))}else{if(jt(a))continue;n(t,a)&&l.push(a)}return l}return r(e)}function jo(e,n,t){const o=new Map;return e.forEach(r=>{Je(r)?r[t].forEach(s=>{o.set(s[n],s)}):o.set(r[n],r)}),o}const Vo=de([H("select",`
 z-index: auto;
 outline: none;
 width: 100%;
 position: relative;
 font-weight: var(--n-font-weight);
 `),H("select-menu",`
 margin: 4px 0;
 box-shadow: var(--n-menu-box-shadow);
 `,[Nt({originalTransition:"background-color .3s var(--n-bezier), box-shadow .3s var(--n-bezier)"})])]),Uo=Object.assign(Object.assign({},xe.props),{to:vt.propTo,bordered:{type:Boolean,default:void 0},clearable:Boolean,clearCreatedOptionsOnClear:{type:Boolean,default:!0},clearFilterAfterSelect:{type:Boolean,default:!0},options:{type:Array,default:()=>[]},defaultValue:{type:[String,Number,Array],default:null},keyboard:{type:Boolean,default:!0},value:[String,Number,Array],placeholder:String,menuProps:Object,multiple:Boolean,size:String,menuSize:{type:String},filterable:Boolean,disabled:{type:Boolean,default:void 0},remote:Boolean,loading:Boolean,filter:Function,placement:{type:String,default:"bottom-start"},widthMode:{type:String,default:"trigger"},tag:Boolean,onCreate:Function,fallbackOption:{type:[Function,Boolean],default:void 0},show:{type:Boolean,default:void 0},showArrow:{type:Boolean,default:!0},maxTagCount:[Number,String],ellipsisTagPopoverProps:Object,consistentMenuWidth:{type:Boolean,default:!0},virtualScroll:{type:Boolean,default:!0},labelField:{type:String,default:"label"},valueField:{type:String,default:"value"},childrenField:{type:String,default:"children"},renderLabel:Function,renderOption:Function,renderTag:Function,"onUpdate:value":[Function,Array],inputProps:Object,nodeProps:Function,ignoreComposition:{type:Boolean,default:!0},showOnFocus:Boolean,onUpdateValue:[Function,Array],onBlur:[Function,Array],onClear:[Function,Array],onFocus:[Function,Array],onScroll:[Function,Array],onSearch:[Function,Array],onUpdateShow:[Function,Array],"onUpdate:show":[Function,Array],displayDirective:{type:String,default:"show"},resetMenuOnOptionsChange:{type:Boolean,default:!0},status:String,showCheckmark:{type:Boolean,default:!0},scrollbarProps:Object,onChange:[Function,Array],items:Array}),Yo=ge({name:"Select",props:Uo,slots:Object,setup(e){const{mergedClsPrefixRef:n,mergedBorderedRef:t,namespaceRef:o,inlineThemeDisabled:r}=Qe(e),s=xe("Select","-select",Vo,Yn,e,n),l=M(e.defaultValue),a=le(e,"value"),d=kt(a,l),c=M(!1),p=M(""),g=Xn(e,["items","options"]),R=M([]),I=M([]),m=K(()=>I.value.concat(R.value).concat(g.value)),w=K(()=>{const{filter:i}=e;if(i)return i;const{labelField:C,valueField:z}=e;return(L,$)=>{if(!$)return!1;const N=$[C];if(typeof N=="string")return ft(L,N);const W=$[z];return typeof W=="string"?ft(L,W):typeof W=="number"?ft(L,String(W)):!1}}),F=K(()=>{if(e.remote)return g.value;{const{value:i}=m,{value:C}=p;return!C.length||!e.filterable?i:Ho(i,w.value,C,e.childrenField)}}),P=K(()=>{const{valueField:i,childrenField:C}=e,z=Do(i,C);return Io(F.value,z)}),k=K(()=>jo(m.value,e.valueField,e.childrenField)),O=M(!1),b=kt(le(e,"show"),O),y=M(null),A=M(null),_=M(null),{localeRef:U}=jn("Select"),q=K(()=>{var i;return(i=e.placeholder)!==null&&i!==void 0?i:U.value.placeholder}),E=[],ee=M(new Map),ie=K(()=>{const{fallbackOption:i}=e;if(i===void 0){const{labelField:C,valueField:z}=e;return L=>({[C]:String(L),[z]:L})}return i===!1?!1:C=>Object.assign(i(C),{value:C})});function h(i){const C=e.remote,{value:z}=ee,{value:L}=k,{value:$}=ie,N=[];return i.forEach(W=>{if(L.has(W))N.push(L.get(W));else if(C&&z.has(W))N.push(z.get(W));else if($){const re=$(W);re&&N.push(re)}}),N}const T=K(()=>{if(e.multiple){const{value:i}=d;return Array.isArray(i)?h(i):[]}return null}),D=K(()=>{const{value:i}=d;return!e.multiple&&!Array.isArray(i)?i===null?null:h([i])[0]||null:null}),B=Vn(e),{mergedSizeRef:Q,mergedDisabledRef:V,mergedStatusRef:X}=B;function G(i,C){const{onChange:z,"onUpdate:value":L,onUpdateValue:$}=e,{nTriggerFormChange:N,nTriggerFormInput:W}=B;z&&ve(z,i,C),$&&ve($,i,C),L&&ve(L,i,C),l.value=i,N(),W()}function Y(i){const{onBlur:C}=e,{nTriggerFormBlur:z}=B;C&&ve(C,i),z()}function ae(){const{onClear:i}=e;i&&ve(i)}function se(i){const{onFocus:C,showOnFocus:z}=e,{nTriggerFormFocus:L}=B;C&&ve(C,i),L(),z&&he()}function u(i){const{onSearch:C}=e;C&&ve(C,i)}function x(i){const{onScroll:C}=e;C&&ve(C,i)}function J(){var i;const{remote:C,multiple:z}=e;if(C){const{value:L}=ee;if(z){const{valueField:$}=e;(i=T.value)===null||i===void 0||i.forEach(N=>{L.set(N[$],N)})}else{const $=D.value;$&&L.set($[e.valueField],$)}}}function fe(i){const{onUpdateShow:C,"onUpdate:show":z}=e;C&&ve(C,i),z&&ve(z,i),O.value=i}function he(){V.value||(fe(!0),O.value=!0,e.filterable&&He())}function ce(){fe(!1)}function Ce(){p.value="",I.value=E}const be=M(!1);function Se(){e.filterable&&(be.value=!0)}function pe(){e.filterable&&(be.value=!1,b.value||Ce())}function Be(){V.value||(b.value?e.filterable?He():ce():he())}function _e(i){var C,z;!((z=(C=_.value)===null||C===void 0?void 0:C.selfRef)===null||z===void 0)&&z.contains(i.relatedTarget)||(c.value=!1,Y(i),ce())}function ke(i){se(i),c.value=!0}function Re(){c.value=!0}function Ee(i){var C;!((C=y.value)===null||C===void 0)&&C.$el.contains(i.relatedTarget)||(c.value=!1,Y(i),ce())}function Ae(){var i;(i=y.value)===null||i===void 0||i.focus(),ce()}function $e(i){var C;b.value&&(!((C=y.value)===null||C===void 0)&&C.$el.contains(Gn(i))||ce())}function Ie(i){if(!Array.isArray(i))return[];if(ie.value)return Array.from(i);{const{remote:C}=e,{value:z}=k;if(C){const{value:L}=ee;return i.filter($=>z.has($)||L.has($))}else return i.filter(L=>z.has(L))}}function Pe(i){ue(i.rawNode)}function ue(i){if(V.value)return;const{tag:C,remote:z,clearFilterAfterSelect:L,valueField:$}=e;if(C&&!z){const{value:N}=I,W=N[0]||null;if(W){const re=R.value;re.length?re.push(W):R.value=[W],I.value=E}}if(z&&ee.value.set(i[$],i),e.multiple){const N=Ie(d.value),W=N.findIndex(re=>re===i[$]);if(~W){if(N.splice(W,1),C&&!z){const re=f(i[$]);~re&&(R.value.splice(re,1),L&&(p.value=""))}}else N.push(i[$]),L&&(p.value="");G(N,h(N))}else{if(C&&!z){const N=f(i[$]);~N?R.value=[R.value[N]]:R.value=E}De(),ce(),G(i[$],i)}}function f(i){return R.value.findIndex(z=>z[e.valueField]===i)}function S(i){b.value||he();const{value:C}=i.target;p.value=C;const{tag:z,remote:L}=e;if(u(C),z&&!L){if(!C){I.value=E;return}const{onCreate:$}=e,N=$?$(C):{[e.labelField]:C,[e.valueField]:C},{valueField:W,labelField:re}=e;g.value.some(me=>me[W]===N[W]||me[re]===N[re])||R.value.some(me=>me[W]===N[W]||me[re]===N[re])?I.value=E:I.value=[N]}}function ne(i){i.stopPropagation();const{multiple:C,tag:z,remote:L,clearCreatedOptionsOnClear:$}=e;!C&&e.filterable&&ce(),z&&!L&&$&&(R.value=E),ae(),C?G([],[]):G(null,null)}function tt(i){!Le(i,"action")&&!Le(i,"empty")&&!Le(i,"header")&&i.preventDefault()}function nt(i){x(i)}function Ke(i){var C,z,L,$,N;if(!e.keyboard){i.preventDefault();return}switch(i.key){case" ":if(e.filterable)break;i.preventDefault();case"Enter":if(!(!((C=y.value)===null||C===void 0)&&C.isComposing)){if(b.value){const W=(z=_.value)===null||z===void 0?void 0:z.getPendingTmNode();W?Pe(W):e.filterable||(ce(),De())}else if(he(),e.tag&&be.value){const W=I.value[0];if(W){const re=W[e.valueField],{value:me}=d;e.multiple&&Array.isArray(me)&&me.includes(re)||ue(W)}}}i.preventDefault();break;case"ArrowUp":if(i.preventDefault(),e.loading)return;b.value&&((L=_.value)===null||L===void 0||L.prev());break;case"ArrowDown":if(i.preventDefault(),e.loading)return;b.value?($=_.value)===null||$===void 0||$.next():he();break;case"Escape":b.value&&(qn(i),ce()),(N=y.value)===null||N===void 0||N.focus();break}}function De(){var i;(i=y.value)===null||i===void 0||i.focus()}function He(){var i;(i=y.value)===null||i===void 0||i.focusInput()}function ot(){var i;b.value&&((i=A.value)===null||i===void 0||i.syncPosition())}J(),Oe(le(e,"options"),J);const rt={focus:()=>{var i;(i=y.value)===null||i===void 0||i.focus()},focusInput:()=>{var i;(i=y.value)===null||i===void 0||i.focusInput()},blur:()=>{var i;(i=y.value)===null||i===void 0||i.blur()},blurInput:()=>{var i;(i=y.value)===null||i===void 0||i.blurInput()}},je=K(()=>{const{self:{menuBoxShadow:i}}=s.value;return{"--n-menu-box-shadow":i}}),we=r?et("select",void 0,je,e):void 0;return Object.assign(Object.assign({},rt),{mergedStatus:X,mergedClsPrefix:n,mergedBordered:t,namespace:o,treeMate:P,isMounted:Un(),triggerRef:y,menuRef:_,pattern:p,uncontrolledShow:O,mergedShow:b,adjustedTo:vt(e),uncontrolledValue:l,mergedValue:d,followerRef:A,localizedPlaceholder:q,selectedOption:D,selectedOptions:T,mergedSize:Q,mergedDisabled:V,focused:c,activeWithoutMenuOpen:be,inlineThemeDisabled:r,onTriggerInputFocus:Se,onTriggerInputBlur:pe,handleTriggerOrMenuResize:ot,handleMenuFocus:Re,handleMenuBlur:Ee,handleMenuTabOut:Ae,handleTriggerClick:Be,handleToggle:Pe,handleDeleteOption:ue,handlePatternInput:S,handleClear:ne,handleTriggerBlur:_e,handleTriggerFocus:ke,handleKeydown:Ke,handleMenuAfterLeave:Ce,handleMenuClickOutside:$e,handleMenuScroll:nt,handleMenuKeydown:Ke,handleMenuMousedown:tt,mergedTheme:s,cssVars:r?void 0:je,themeClass:we?.themeClass,onRender:we?.onRender})},render(){return v("div",{class:`${this.mergedClsPrefix}-select`},v(Kn,null,{default:()=>[v(Dn,null,{default:()=>v(Ko,{ref:"triggerRef",inlineThemeDisabled:this.inlineThemeDisabled,status:this.mergedStatus,inputProps:this.inputProps,clsPrefix:this.mergedClsPrefix,showArrow:this.showArrow,maxTagCount:this.maxTagCount,ellipsisTagPopoverProps:this.ellipsisTagPopoverProps,bordered:this.mergedBordered,active:this.activeWithoutMenuOpen||this.mergedShow,pattern:this.pattern,placeholder:this.localizedPlaceholder,selectedOption:this.selectedOption,selectedOptions:this.selectedOptions,multiple:this.multiple,renderTag:this.renderTag,renderLabel:this.renderLabel,filterable:this.filterable,clearable:this.clearable,disabled:this.mergedDisabled,size:this.mergedSize,theme:this.mergedTheme.peers.InternalSelection,labelField:this.labelField,valueField:this.valueField,themeOverrides:this.mergedTheme.peerOverrides.InternalSelection,loading:this.loading,focused:this.focused,onClick:this.handleTriggerClick,onDeleteOption:this.handleDeleteOption,onPatternInput:this.handlePatternInput,onClear:this.handleClear,onBlur:this.handleTriggerBlur,onFocus:this.handleTriggerFocus,onKeydown:this.handleKeydown,onPatternBlur:this.onTriggerInputBlur,onPatternFocus:this.onTriggerInputFocus,onResize:this.handleTriggerOrMenuResize,ignoreComposition:this.ignoreComposition},{arrow:()=>{var e,n;return[(n=(e=this.$slots).arrow)===null||n===void 0?void 0:n.call(e)]}})}),v(Hn,{ref:"followerRef",show:this.mergedShow,to:this.adjustedTo,teleportDisabled:this.adjustedTo===vt.tdkey,containerClass:this.namespace,width:this.consistentMenuWidth?"target":void 0,minWidth:"target",placement:this.placement},{default:()=>v(Et,{name:"fade-in-scale-up-transition",appear:this.isMounted,onAfterLeave:this.handleMenuAfterLeave},{default:()=>{var e,n,t;return this.mergedShow||this.displayDirective==="show"?((e=this.onRender)===null||e===void 0||e.call(this),kn(v(Bo,Object.assign({},this.menuProps,{ref:"menuRef",onResize:this.handleTriggerOrMenuResize,inlineThemeDisabled:this.inlineThemeDisabled,virtualScroll:this.consistentMenuWidth&&this.virtualScroll,class:[`${this.mergedClsPrefix}-select-menu`,this.themeClass,(n=this.menuProps)===null||n===void 0?void 0:n.class],clsPrefix:this.mergedClsPrefix,focusable:!0,labelField:this.labelField,valueField:this.valueField,autoPending:!0,nodeProps:this.nodeProps,theme:this.mergedTheme.peers.InternalSelectMenu,themeOverrides:this.mergedTheme.peerOverrides.InternalSelectMenu,treeMate:this.treeMate,multiple:this.multiple,size:this.menuSize,renderOption:this.renderOption,renderLabel:this.renderLabel,value:this.mergedValue,style:[(t=this.menuProps)===null||t===void 0?void 0:t.style,this.cssVars],onToggle:this.handleToggle,onScroll:this.handleMenuScroll,onFocus:this.handleMenuFocus,onBlur:this.handleMenuBlur,onKeydown:this.handleMenuKeydown,onTabOut:this.handleMenuTabOut,onMousedown:this.handleMenuMousedown,show:this.mergedShow,showCheckmark:this.showCheckmark,resetMenuOnOptionsChange:this.resetMenuOnOptionsChange,scrollbarProps:this.scrollbarProps}),{empty:()=>{var o,r;return[(r=(o=this.$slots).empty)===null||r===void 0?void 0:r.call(o)]},header:()=>{var o,r;return[(r=(o=this.$slots).header)===null||r===void 0?void 0:r.call(o)]},action:()=>{var o,r;return[(r=(o=this.$slots).action)===null||r===void 0?void 0:r.call(o)]}}),this.displayDirective==="show"?[[Rn,this.mergedShow],[St,this.handleMenuClickOutside,void 0,{capture:!0}]]:[[St,this.handleMenuClickOutside,void 0,{capture:!0}]])):null}})})]}))}});export{ro as F,Yo as N,to as V,ut as a,Bo as b,Io as c,Do as d,Ft as g,Le as h,at as m};
